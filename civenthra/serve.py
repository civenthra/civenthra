# -*- coding: utf-8 -*-
"""serve · 本地网页界面（只绑 127.0.0.1，不监听外网）。

做成网页而不是只留命令行，是因为「拔掉网线」这件事需要被看见：
页面展示当前 Python 进程的出站限制状态，答案下面挂出一排引文标签。网页服务、
推理服务和语音 sidecar 默认通过回环地址通信；严格离线仍需系统级网络策略或物理断网。
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import airgap
from . import config as C
from . import voice
from .embed import EmbedServer
from .index import IndexBundle
from .llm import LlamaServer
from .local_api import (
    TOKEN_ENV, TOKEN_HEADER, host_header_is_local, is_loopback_host,
    new_session_token, origin_matches, token_matches,
)
from .rag import Civenthra, format_citations

MAX_JSON_BODY = 64 * 1024
MAX_REPORT_BODY = 8 * 1024
MAX_PCM_BODY = 16 * 1024 * 1024


class RequestRejected(Exception):
    """Internal control flow after an HTTP error response has been sent."""

# ── 共享语音核心 ────────────────────────────────────────────────────────────
# 主页面和语音自检页（/voicecheck）**用同一份**：自检页才有意义 ——
# 若两边各写一份，自检跑通不代表线上跑得通。
VOICE_CORE = r"""
/* 此随机值只用于阻止跨站网页调用回环 API，不是长期凭据。 */
const CIVENTHRA_SESSION_TOKEN = /*__SESSION_TOKEN__*/;
const CIVENTHRA_VOICE_DIAGNOSTICS = /*__VOICE_DIAGNOSTICS__*/;
const civenthraNativeFetch = window.fetch.bind(window);
window.fetch = function(input, init){
  const options = Object.assign({}, init || {});
  const target = new URL(input, window.location.href);
  if(target.origin === window.location.origin && target.pathname.startsWith('/api/')){
    const headers = new Headers(options.headers || {});
    headers.set('X-Civenthra-Token', CIVENTHRA_SESSION_TOKEN);
    options.headers = headers;
  }
  return civenthraNativeFetch(input, options);
};

/* 任意采样率 → 16kHz 单声道 int16。浏览器 AudioContext 的采样率由系统决定
   （本机常是 48k），服务端认 16k PCM，所以这一步必须做，且省掉一次编解码。 */
function to16k(chunks, sr){
  let n = 0; for(const c of chunks) n += c.length;
  if(!n) return new ArrayBuffer(0);
  const all = new Float32Array(n); let o = 0;
  for(const c of chunks){ all.set(c, o); o += c.length; }
  const ratio = sr / 16000, outN = Math.floor(n / ratio), out = new Int16Array(outN);
  for(let i=0;i<outN;i++){
    const p = i*ratio, i0 = Math.floor(p), i1 = Math.min(i0+1, n-1), f = p-i0;
    let v = all[i0]*(1-f) + all[i1]*f;
    v = v < -1 ? -1 : (v > 1 ? 1 : v);
    out[i] = v < 0 ? v*0x8000 : v*0x7fff;
  }
  return out.buffer;
}

/* 录音机。onLevel(0..1) 给电平条，onState('rec'|'wait'|'idle'|'error') 给 UI，
   onDone(text, seconds, ms, err) 给结果。免按键模式下靠 RMS 静音自动收。 */
class Recorder{
  constructor(o){
    o = o || {};
    this.onLevel = o.onLevel || function(){};
    this.onState = o.onState || function(){};
    this.onDone  = o.onDone  || function(){};
    this.rec = false; this.sr = 16000; this.chunks = []; this.peak = 0;
    this.voicedAt = 0; this.silAt = 0;
    this.handsFree = false; this.maxMs = 30000;
    this.silenceMs = 1200; this.threshold = 0.018;
    this.maxLevel = 0; this.label = ''; this.settings = null;
  }
  async start(o){
    o = o || {};
    if(this.rec) return true;
    this.handsFree = !!o.handsFree;
    try{
      this.stream = await navigator.mediaDevices.getUserMedia({audio:{
        channelCount:1, echoCancellation:true, noiseSuppression:true, autoGainControl:true}});
    }catch(e){
      /* 光说「不可用」没法排障 —— 到底是权限、没设备，还是被别家占了？
         这里把 DOMException.name 翻译成人话，并原样附在括号里备查。 */
      const n = (e && e.name) || String(e);
      const why = ({
        NotAllowedError:      '麦克风权限被拒绝（或页面不在安全上下文）',
        PermissionDeniedError:'麦克风权限被拒绝',
        NotFoundError:        '系统里没有可用的麦克风设备',
        DevicesNotFoundError: '系统里没有可用的麦克风设备',
        NotReadableError:     '麦克风被其它程序占用',
        TrackStartError:      '麦克风被其它程序占用',
        OverconstrainedError: '麦克风不支持所要求的参数',
        SecurityError:        '页面不在安全上下文（必须用 http://127.0.0.1 打开）',
        AbortError:           '浏览器中止了取流'
      })[n] || '取流失败';
      this.lastError = n;
      this.onState('error', '麦克风不可用：' + why + '〔' + n + '〕');
      return false;
    }
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ac = new AC();
    this.sr = this.ac.sampleRate;
    try{                                    // 记住浏览器到底挑了哪个麦克风
      const tr = this.stream.getAudioTracks()[0];
      if(tr){ this.label = tr.label || '';
              this.settings = tr.getSettings ? tr.getSettings() : null; }
    }catch(e){}
    this.src = this.ac.createMediaStreamSource(this.stream);
    this.proc = this.ac.createScriptProcessor(4096, 1, 1);
    this.chunks = []; this.peak = 0; this.voicedAt = 0; this.silAt = 0;
    this.maxLevel = 0;
    const self = this;
    this.proc.onaudioprocess = function(e){
      if(!self.rec) return;
      const buf = e.inputBuffer.getChannelData(0);
      self.chunks.push(new Float32Array(buf));
      let s = 0; for(let i=0;i<buf.length;i+=4) s += buf[i]*buf[i];
      const rms = Math.sqrt(s / (buf.length/4));
      if(rms > self.maxLevel) self.maxLevel = rms;
      self.peak = Math.max(self.peak*0.90, rms);
      self.onLevel(Math.min(1, self.peak*4));
      const now = performance.now();
      if(rms > self.threshold){ self.voicedAt = now; self.silAt = 0; }
      else if(self.voicedAt && !self.silAt){ self.silAt = now; }
      if(self.handsFree && self.silAt && now - self.silAt > self.silenceMs) self.stop();
      if(now - self.startedAt > self.maxMs) self.stop();
    };
    this.sink = this.ac.createGain(); this.sink.gain.value = 0;   // 别把自己的声音放出来
    this.src.connect(this.proc); this.proc.connect(this.sink); this.sink.connect(this.ac.destination);
    this.rec = true; this.startedAt = performance.now();
    this.onState('rec');
    return true;
  }
  _teardown(){
    try{ this.proc.disconnect(); this.src.disconnect(); this.sink.disconnect(); }catch(e){}
    try{ this.stream.getTracks().forEach(t=>t.stop()); }catch(e){}
    try{ this.ac.close(); }catch(e){}
  }
  cancel(){ if(!this.rec) return; this.rec = false; this._teardown();
            this.chunks = []; this.onState('idle'); }
  async stop(){
    if(!this.rec) return;
    this.rec = false;
    const dur = (performance.now() - this.startedAt) / 1000;
    this.onState('wait');
    this._teardown();
    const pcm = to16k(this.chunks, this.sr); this.chunks = [];
    if(dur < 0.35 || pcm.byteLength < 3200){
      this.report(dur, pcm.byteLength, '', null, 0, 'too_short');
      this.onState('idle'); this.onDone('', dur, 0, null); return;
    }
    const t0 = performance.now();
    let text = '', err = null;
    try{
      const r = await fetch('/api/voice/asr', {method:'POST',
        headers:{'Content-Type':'application/octet-stream','X-Sample-Rate':'16000'}, body: pcm});
      const d = await r.json();
      if(d.error) err = d.error; else text = d.text || '';
    }catch(e){ err = '识别失败：'+e; }
    const ms = Math.round(performance.now()-t0);
    this.report(dur, pcm.byteLength, text, err, ms, 'ok');
    this.onState('idle');
    this.onDone(text, dur, ms, err);
  }
  /* ★ 把「浏览器这一侧实际录到了什么」报给服务端。
     没有这一步，服务端只看得见「收到 N 字节」或「识别出空串」，
     分不清是**没录到声音**、还是**录到了但识别不出来** —— 这两件事的修法完全相反。 */
  report(dur, bytes, text, err, ms, why){
    if(!CIVENTHRA_VOICE_DIAGNOSTICS) return;
    try{
      fetch('/api/voice/report', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({where: location.pathname, why: why,
          dur: +dur.toFixed(2), bytes: bytes, sr: this.sr,
          maxLevel: +this.maxLevel.toFixed(5),
          textLength: (text || '').length, err: (err ? String(err) : ''), ms: ms || 0})});
    }catch(e){}
  }
}

/* 朗读器。回答是流式的，所以按句喂进来、边合成边播，不等整段说完。 */
function cleanForSpeech(s){
  return s.replace(/\[\d{1,2}\]/g,'')        // 引用编号念出来很吵
          .replace(/[#*`>_~]/g,'')
          .replace(/\s+/g,' ').trim();
}
function playBlob(blob, slot){
  return new Promise(res=>{
    const url = URL.createObjectURL(blob);
    const a = new Audio(url);
    slot.audio = a; slot.resolve = res;
    a.onended = a.onerror = ()=>{ URL.revokeObjectURL(url); res(); };
    a.play().catch(()=>{ URL.revokeObjectURL(url); res(); });
  });
}
class Speaker{
  constructor(o){
    o = o || {};
    this.onActive = o.onActive || function(){};
    this.q = []; this.buf = ''; this.playing = false;
    this.slot = { audio:null, resolve:null };
    this.lastEngine = '';
  }
  reset(){ this.buf = ''; this.stop(); }
  feed(piece, engine){
    this.buf += piece;
    let i;
    while((i = this.buf.search(/[。！？!?；;\n]/)) >= 0){
      const s = cleanForSpeech(this.buf.slice(0, i+1));
      this.buf = this.buf.slice(i+1);
      if(s.length >= 2) this.q.push([s, engine]);
    }
    this._pump();
  }
  flush(engine){
    const s = cleanForSpeech(this.buf); this.buf = '';
    if(s.length >= 2) this.q.push([s, engine]);
    this._pump();
  }
  async _pump(){
    if(this.playing || !this.q.length) return;
    this.playing = true; this.onActive(true);
    while(this.q.length){
      const item = this.q.shift();
      try{
        const r = await fetch('/api/voice/tts', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({text: item[0], engine: item[1]})});
        if(!r.ok) continue;
        const blob = await r.blob();
        this.lastEngine = r.headers.get('X-Voice-Engine') || '';
        await playBlob(blob, this.slot);
      }catch(e){ /* 朗读失败不影响阅读，静默跳过这一句 */ }
    }
    this.playing = false; this.onActive(false);
  }
  stop(){
    this.q = [];
    if(this.slot.audio){ try{ this.slot.audio.pause(); }catch(e){} this.slot.audio = null; }
    if(this.slot.resolve){ const r = this.slot.resolve; this.slot.resolve = null; r(); }
    this.playing = false; this.onActive(false);
  }
}

async function fetchVoiceInfo(tries, onWait){
  tries = tries || 0;
  const d = await (await fetch('/api/voice/info')).json();
  if(d.starting && tries < 15){                    // sidecar 在后台拉起，等它
    if(onWait) onWait(d);
    await new Promise(r=>setTimeout(r, 1500));
    return fetchVoiceInfo(tries+1, onWait);
  }
  return d;
}
"""


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>文明穹顶 · civenthra</title>
<style>
:root{--bg:#0e0f13;--panel:#16181f;--line:#262a35;--fg:#e8e6e3;--dim:#8b90a0;
      --ember:#ff7a18;--ember2:#ffb547;--ok:#3ddc97;--bad:#ff5c5c;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font-family:-apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
     min-height:100vh;display:flex;flex-direction:column}
header{padding:18px 26px;border-bottom:1px solid var(--line);
       display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.logo{font-size:22px;font-weight:700;letter-spacing:.5px}
.logo span{color:var(--ember)}
.pill{margin-left:auto;display:flex;gap:10px;align-items:center;font-size:13px}
.st{padding:4px 12px;border-radius:999px;border:1px solid var(--line);
    background:#11131a;display:flex;align-items:center;gap:7px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);
     box-shadow:0 0 8px var(--ok)}
.dot.off{background:var(--ember);box-shadow:0 0 10px var(--ember)}
main{flex:1;display:flex;gap:18px;padding:22px 26px;max-width:1400px;width:100%;margin:0 auto}
@media(max-width:980px){main{flex-direction:column}}
.col{flex:1;min-width:0;display:flex;flex-direction:column;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
.card h2{margin:0 0 12px;font-size:14px;letter-spacing:1px;color:var(--dim);
         font-weight:600;text-transform:uppercase}
.row{display:flex;gap:10px}
input{flex:1;background:#0b0d12;border:1px solid var(--line);border-radius:10px;
      color:var(--fg);padding:13px 15px;font-size:15px;outline:none;font-family:inherit}
input:focus{border-color:var(--ember)}
button{background:linear-gradient(180deg,var(--ember),#e06500);border:0;color:#fff;
       border-radius:10px;padding:0 24px;font-size:15px;font-weight:600;cursor:pointer;
       min-height:46px}
button:disabled{opacity:.5;cursor:default}
.ans{white-space:pre-wrap;line-height:1.85;font-size:15.5px;min-height:150px}
.ans .ph{color:var(--dim)}
.cites{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.chip{background:#1d2029;border:1px solid var(--line);border-radius:999px;
      padding:5px 13px;font-size:12.5px;color:var(--ember2)}
.meta{color:var(--dim);font-size:12.5px;margin-top:10px}
.src{font-size:13.5px;line-height:1.7;max-height:520px;overflow:auto}
.src details{margin-bottom:10px;background:#11131a;border:1px solid var(--line);
             border-radius:10px;padding:10px 13px}
.src summary{cursor:pointer;color:var(--ember2);font-size:13px}
.src p{margin:9px 0 0;color:#c9c7c3;white-space:pre-wrap}
.banner{margin:0 26px 14px;padding:11px 16px;border-radius:10px;font-size:13.5px;
        background:linear-gradient(90deg,rgba(255,122,24,.14),transparent);
        border:1px solid rgba(255,122,24,.35)}
.banner b{color:var(--ember)}
/* ── 语音 ───────────────────────────────────────────────────────────── */
.vrow{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap}
#mic{flex:0 0 auto;min-width:150px;background:linear-gradient(180deg,#2b2f3a,#22252e);
     border:1px solid var(--line);color:var(--fg);display:flex;align-items:center;gap:8px;
     justify-content:center;user-select:none;touch-action:none}
#mic.rec{background:linear-gradient(180deg,#c62b2b,#8f1d1d);border-color:#e04a4a;
         animation:pulse 1.1s ease-in-out infinite}
#mic.wait{background:linear-gradient(180deg,#3a3320,#2b2718);border-color:#7a6535}
#mic:disabled{opacity:.4}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(224,74,74,.55)}
                 50%{box-shadow:0 0 0 7px rgba(224,74,74,0)}}
.lvl{flex:1;height:6px;border-radius:3px;background:#11131a;overflow:hidden;min-width:60px}
.lvl i{display:block;height:100%;width:0;background:linear-gradient(90deg,#3ddc97,#ffb547,#ff5c5c);
       transition:width .07s linear}
.opt{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--dim);cursor:pointer;
     user-select:none;white-space:nowrap}
.opt input{flex:0 0 auto;width:auto;margin:0;accent-color:var(--ember)}
select{background:#0b0d12;border:1px solid var(--line);border-radius:8px;color:var(--dim);
       padding:6px 8px;font-size:12.5px;font-family:inherit;outline:none}
.vst{font-size:12.5px;color:var(--dim)}
.vst b{color:var(--ember2);font-weight:600}
.spk{margin-left:auto;background:#23262f;min-height:34px;padding:0 12px;font-size:12.5px;
     font-weight:500;border:1px solid var(--line);flex:0 0 auto}
.spk.on{background:linear-gradient(180deg,var(--ember),#e06500);border-color:transparent}
.speaking{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--ember2);
          margin-right:5px;animation:pulse 1s infinite}
.ans .cur:after{content:"▍";color:var(--ember);animation:pulse 1s infinite}
</style></head><body>
<header>
  <div class="logo">🌐 文明<span>穹顶</span></div>
  <div class="pill">
    <div class="st"><span class="dot off" id="net"></span><span id="netTxt">进程出站限制 · 已启用</span></div>
    <div class="st" id="kb"></div>
    <div class="st" id="vc"><span class="dot off" id="vdot"></span><span id="vTxt">语音 · 未就绪</span></div>
  </div>
</header>
<div class="banner" id="banner">
  当前 Python 进程已启用<b>出站连接限制</b>；这不是整机防火墙或物理断网。
  回答依据来自本机文明穹顶文献。默认语音识别与系统语音均在本机运行；严格隔离部署仍须
  配合操作系统防火墙、隔离网络或物理断网。
</div>
<main>
  <div class="col">
    <div class="card">
      <h2>提问</h2>
      <div class="row">
        <input id="q" placeholder="人类的本质是什么？" autocomplete="off">
        <button id="go">提问</button>
      </div>
      <div class="row" style="margin-top:10px">
        <button id="cmp" style="background:#23262f;flex:1">对照：不检索（裸模型）</button>
        <button id="clr" style="background:#23262f;flex:1">清空</button>
      </div>
      <div class="vrow">
        <button id="mic" disabled>🎙 <span id="micTxt">按住说话</span></button>
        <div class="lvl"><i id="lvl"></i></div>
        <label class="opt"><input type="checkbox" id="handsFree"> 免按键（说完自动停）</label>
      </div>
      <div class="vrow">
        <button id="wake" style="background:#23262f" disabled>🔮 <span id="wakeTxt">常听 · 说「小雅小雅」唤醒</span></button>
        <label class="opt"><input type="checkbox" id="wakeBeep" checked> 唤醒时“叮”一声</label>
      </div>
      <div class="vrow">
        <label class="opt"><input type="checkbox" id="autoSpeak" checked> 🔊 自动朗读回答</label>
        <label class="opt">音色
          <select id="eng">
            <option value="xiaoya" selected>小雅（XTTS 克隆 · 离线）</option>
            <option value="sapi">系统中文语音（离线）</option>
            <option value="edge">edge-tts 晓晓（更自然 · 需联网）</option>
          </select>
        </label>
        <span class="vst" id="vst"></span>
      </div>
    </div>
    <div class="card">
      <h2 style="display:flex;align-items:center;gap:10px">回答
        <button id="spk" class="spk" style="display:none">🔇 停止朗读</button>
      </h2>
      <div class="ans" id="ans"><span class="ph">等待提问…</span></div>
      <div class="cites" id="cites"></div>
      <div class="meta" id="meta"></div>
    </div>
  </div>
  <div class="col" style="flex:0 0 440px">
    <div class="card" style="flex:1">
      <h2>召回的原文</h2>
      <div class="src" id="src"><span class="ph" style="color:#8b90a0">提问后显示</span></div>
    </div>
  </div>
</main>
<script>
/*__VOICE_CORE__*/
const $ = s => document.querySelector(s);
const clear = e => e.replaceChildren();
let busy = false, mode = 'civenthra';

async function loadInfo(){
  try{
    const r = await fetch('/api/info'); const d = await r.json();
    $('#kb').textContent = d.chunks + ' 块 · ' + d.books + ' 本 · ' + (d.dense?'向量+BM25':'BM25');
  }catch(e){ $('#kb').textContent = '索引未就绪'; }
}

async function ask(useMode){
  const q = $('#q').value.trim(); if(!q || busy) return;
  busy = true; $('#go').disabled = true;
  $('#ans').textContent = ''; clear($('#cites')); clear($('#src'));
  $('#meta').textContent = '';
  ttsReset();
  const t0 = performance.now();
  try{
    const resp = await fetch('/api/ask', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question:q, mode:useMode||mode})});
    const rd = resp.body.getReader(); const dec = new TextDecoder();
    let buf = '', text = '';
    while(true){
      const {done, value} = await rd.read(); if(done) break;
      buf += dec.decode(value, {stream:true});
      const parts = buf.split('\n\n'); buf = parts.pop();
      for(const p of parts){
        const ln = p.split('\n').find(x=>x.startsWith('data:'));
        if(!ln) continue;
        const ev = JSON.parse(ln.slice(5).trim());
        if(ev.type === 'passages'){ renderSrc(ev.passages); }
        else if(ev.type === 'delta'){ text += ev.text; $('#ans').textContent = text; feedTTS(ev.text); }
        else if(ev.type === 'done'){}
      }
    }
    const dt = ((performance.now()-t0)/1000).toFixed(1);
    $('#meta').textContent = '用时 ' + dt + ' 秒 · 本地回环服务 · 进程出站限制状态见页首';
    ttsFlush();
  }catch(e){
    $('#ans').textContent = '出错了：' + e;
  }
  busy = false; $('#go').disabled = false;
}

function renderSrc(ps){
  const src = $('#src');
  clear(src);
  if(!ps.length){
    const empty = document.createElement('span');
    empty.className = 'ph';
    empty.textContent = '本轮未检索文献';
    src.appendChild(empty);
    return;
  }
  const c = $('#cites');
  clear(c);
  ps.forEach((p, i) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = '[' + (i + 1) + '] ' + String(p.title || '');
    c.appendChild(chip);

    const details = document.createElement('details');
    details.open = i === 0;
    const summary = document.createElement('summary');
    summary.textContent = '[' + (i + 1) + '] 《' + String(p.title || '') + '》'
      + (p.author ? ' · ' + String(p.author) : '')
      + ' · ' + String(p.chapter || '') + '　相关度 ' + String(p.score ?? '');
    const paragraph = document.createElement('p');
    paragraph.textContent = String(p.text || '');
    details.append(summary, paragraph);
    src.appendChild(details);
  });
}

/* ══════════════════════════════════════════════════════════════════
   语音对话
   链路：麦克风(Web Audio 抓原始 PCM) → /api/voice/asr → faster-whisper
          回答文本 → 按句切 → /api/voice/tts → 系统语音，边合成边播
   默认走 127.0.0.1；edge-tts 在进程出站限制启用时由 sidecar 拒绝。
   录音/播放的实现在 VOICE_CORE 里，与 /voicecheck 自检页**共用同一份代码**。
   ══════════════════════════════════════════════════════════════════ */
const V = { ok:false, handsFree:false, lastEngine:'', speaking:false };
const rec = new Recorder({
  onLevel: v => { $('#lvl').style.width = (v*100).toFixed(0) + '%'; },
  onState: (s, msg) => {
    if(s === 'rec'){
      $('#mic').classList.add('rec');
      $('#micTxt').textContent = V.handsFree ? '再点一下结束 · 正在听' : '松开结束 · 正在听';
    }else if(s === 'wait'){
      $('#mic').classList.remove('rec'); $('#mic').classList.add('wait');
      $('#micTxt').textContent = '识别中…';
    }else{
      $('#mic').classList.remove('rec','wait');
      $('#micTxt').textContent = '按住说话'; $('#lvl').style.width = '0%';
      if(msg) $('#vst').textContent = msg;
    }
  },
  onDone: (text, dur, ms, err) => {
    if(err){ $('#vst').textContent = err; return; }
    if(!text){ $('#vst').textContent = '没听清，再试一次'; return; }
    $('#q').value = text;
    $('#vst').textContent = '听到「' + text + '」 · ' + ms + 'ms';
    ask('civenthra');                     // 语音问 → 自动答 → 自动读，闭环
  }
});
const spk = new Speaker({ onActive: on => {
  $('#spk').style.display = on ? 'inline-block' : 'none';
  /* ★ 常听模式必须知道「我正在朗读」——否则它会听见自己的声音把自己唤醒。
     停读后再多闭 600ms，躲掉扬声器的拖尾。 */
  if(on){ V.speaking = true; }
  else { setTimeout(()=>{ V.speaking = false; }, 600); }
} });
const speakEngine = () => $('#eng').value;

async function loadVoice(){
  try{
    const d = await fetchVoiceInfo(0, w => {
      $('#vdot').className = 'dot off';
      $('#vTxt').textContent = '语音 · 启动中';
      $('#vst').textContent = w.hint || '';
    });
    V.ok = !!d.ok;
    $('#vdot').className = 'dot' + (V.ok ? '' : ' off');
    $('#vTxt').textContent = V.ok ? '语音 · 就绪' : '语音 · 未就绪';
    $('#mic').disabled = !V.ok;
    $('#wake').disabled = !V.ok;
    if(V.ok){
      const a = (d.info && d.info.asr) || {}, t = (d.info && d.info.tts) || {};
      const spk = (t.sapi && t.sapi[0] || '').replace(/^Microsoft\s*/,'').replace(/\s*-\s*(Chinese|English).*$/,'');
      $('#vst').textContent = '识别 ' + (a.model||'') + ' / ' + (a.device||'')
        + ' · 朗读 ' + spk + (t.edge ? '（可切 edge）' : '');
    }else{
      $('#vst').textContent = d.hint || '语音服务未启动';
    }
  }catch(e){
    V.ok = false;
    $('#vdot').className='dot off'; $('#vTxt').textContent='语音 · 未就绪';
    $('#vst').textContent='语音服务未启动'; $('#mic').disabled = true; $('#wake').disabled = true;
  }
}

/* ── 录音/朗读：这里只做 UI 接线，逻辑在 VOICE_CORE ───────────────── */
function startRec(handsFree){
  if(!V.ok || rec.rec) return;
  V.handsFree = !!handsFree;
  rec.start({handsFree: V.handsFree});
}
function stopRec(){ rec.stop(); }

/* ── 朗读：UI 接线 ────────────────────────────────────────────────── */
function ttsReset(){ spk.reset(); }
function feedTTS(piece){ if($('#autoSpeak').checked) spk.feed(piece, speakEngine()); }
function ttsFlush(){ if($('#autoSpeak').checked) spk.flush(speakEngine()); }

/* ── 绑定 ─────────────────────────────────────────────────────────── */
const micBtn = $('#mic');
micBtn.addEventListener('pointerdown', e=>{
  e.preventDefault();
  const hf = $('#handsFree').checked;
  if(rec.rec && hf){ rec.stop(); return; }        // 免按键模式下再点一下 = 提前结束
  startRec(hf);
});
micBtn.addEventListener('pointerup', e=>{ e.preventDefault(); if(rec.rec && !V.handsFree) stopRec(); });
micBtn.addEventListener('pointerleave', ()=>{ if(rec.rec && !V.handsFree) stopRec(); });

function typingIn(e){
  const t = e.target || document.activeElement;
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
}
document.addEventListener('keydown', e=>{
  if(e.code !== 'Space' || e.repeat || typingIn(e)) return;
  e.preventDefault(); startRec(false);
});
document.addEventListener('keyup', e=>{
  if(e.code !== 'Space' || typingIn(e)) return;
  stopRec();
});

$('#spk').onclick = ()=>spk.stop();
$('#wake').onclick = ()=>wakeToggle();
$('#autoSpeak').onchange = e => { if(!e.target.checked) spk.stop(); };
$('#eng').onchange = () => {
  const v = $('#eng').value;
  $('#vst').textContent =
      v === 'edge'   ? 'edge 音色需联网；断网会自动回落到系统语音'
    : v === 'xiaoya' ? '小雅音色（XTTS 克隆，离线）· 首次朗读要加载模型约 8 秒，之后很快'
                     : '系统中文语音（离线）';
};

/* ── 常听模式：说「小雅小雅」唤醒 ────────────────────────────────────────
   打法：麦克风常开 → 客户端按 RMS 切句 → 每句送 /api/voice/asr →
        文本里命中唤醒词就「叮」一声进入聆听态，聆听态里的下一句直接当问题。
   ★ 为什么复用 ASR 而不上专用唤醒词模型：本机没装关键词模型（vosk 缺中文包），
     而复用已有 ASR 只多花约 0.5 秒/句，且中文识别质量是最好的。
   ★ 朗读期间必须闭麦，否则它会听见自己的声音、把自己叫醒，变成自问自答。 */
const W = { on:false, state:'idle', until:0, chunks:[], sr:16000,
            sp:null, st:null, ac:null, silAt:0, spokeAt:0, n:0 };

function wakeBeep(){
  if(!$('#wakeBeep').checked) return;
  try{
    const c = new (window.AudioContext||window.webkitAudioContext)();
    const o = c.createOscillator(), g = c.createGain();
    o.frequency.value = 920; o.connect(g); g.connect(c.destination);
    g.gain.value = 0.06; o.start();
    setTimeout(function(){ try{ o.stop(); c.close(); }catch(e){} }, 150);
  }catch(e){}
}

async function wakeProbe(seg){
  const pcm = to16k(seg, W.sr);
  if(pcm.byteLength < 6400) return;                 // <0.2 秒，多半是咳嗽
  let text = '';
  try{
    const r = await fetch('/api/voice/asr', {method:'POST',
      headers:{'Content-Type':'application/octet-stream','X-Sample-Rate':'16000'}, body: pcm});
    const d = await r.json();
    if(d && d.text) text = d.text;
  }catch(e){ return; }
  if(!text) return;
  const hit = matchWake(text);
  const listening = (W.state === 'listen') && performance.now() < W.until;
  if(hit !== null){
    wakeBeep();
    if(hit){                                       // 「小雅小雅，XXX」一句说完
      W.state = 'idle'; $('#vst').textContent = '唤醒 + 提问：「' + text + '」';
      $('#q').value = hit; ask('civenthra'); return;
    }
    W.state = 'listen'; W.until = performance.now() + 8000;
    $('#wakeTxt').textContent = '在听 · 请说';
    $('#vst').textContent = '唤醒词命中：「' + text + '」';
    return;
  }
  if(listening){
    W.state = 'idle';
    $('#vst').textContent = '听到「' + text + '」';
    $('#q').value = text; ask('civenthra');
  }else{
    $('#wakeTxt').textContent = '常听中 · 说「小雅小雅」唤醒';
    $('#vst').textContent = '（背景音：「' + text.slice(0, 20) + '」，未唤醒）';
  }
}

/* 唤醒词匹配要宽容：ASR 常把「小雅」写成「小鸭 / 小呀 / 小亚」，甚至「晓雅」。
   命中两次（小雅小雅）无条件算唤醒；命中一次且整句很短（≤8 字）也算。 */
function matchWake(t){
  const s = (t || '').replace(/[ ，,。.！!？?、:：；;"'“”‘’]/g, '');
  const m = s.match(/[小晓肖笑筱][雅呀鸭牙亚芽]/g);
  if(!m) return null;
  const i = s.search(/[小晓肖笑筱][雅呀鸭牙亚芽]/);
  const rest = k => s.slice(i + k).replace(/^[，,。.！!？?、]+/, '').trim();
  if(m.length >= 2) return rest(4);
  if(s.length <= 8)  return rest(2);
  return null;
}

async function wakeToggle(){
  if(W.on){ wakeStop(); return; }
  try{
    W.st = await navigator.mediaDevices.getUserMedia({audio:{
      channelCount:1, echoCancellation:false, noiseSuppression:false, autoGainControl:false}});
  }catch(e){
    const n = (e && e.name) || String(e);
    $('#vst').textContent = '常听模式起不来：' + n + '〔' + (n === 'NotAllowedError'
      ? '麦克风权限被拒' : '见 /voicecheck 自检') + '〕';
    return;
  }
  const AC = window.AudioContext || window.webkitAudioContext;
  W.ac = new AC(); W.sr = W.ac.sampleRate;
  try{ await W.ac.resume(); }catch(e){}
  const src = W.ac.createMediaStreamSource(W.st);
  const pr  = W.ac.createScriptProcessor(4096, 1, 1);
  const sink = W.ac.createGain(); sink.gain.value = 0;
  W.sp = {pr:pr, src:src, sink:sink};
  W.chunks = []; W.silAt = 0; W.spokeAt = 0;
  pr.onaudioprocess = function(e){
    if(!W.on || V.speaking || busy) return;         // 别听见自己，别打断回答
    const b = e.inputBuffer.getChannelData(0);
    let s = 0; for(let i = 0; i < b.length; i += 4) s += b[i] * b[i];
    const rms = Math.sqrt(s / (b.length / 4));
    const now = performance.now();
    $('#lvl').style.width = Math.min(100, rms * 400).toFixed(0) + '%';
    if(rms > 0.010){ W.spokeAt = now; W.silAt = 0; }
    else if(W.spokeAt && !W.silAt){ W.silAt = now; }
    if(W.spokeAt) W.chunks.push(new Float32Array(b));
    if(W.spokeAt && W.silAt && now - W.silAt > 900){      // 静 0.9 秒 = 一句说完
      const seg = W.chunks; W.chunks = []; W.spokeAt = 0; W.silAt = 0;
      const secs = seg.reduce((a, c) => a + c.length, 0) / W.sr;
      if(secs >= 0.35) wakeProbe(seg);
    }
    if(W.chunks.length > 400){ W.chunks = []; W.spokeAt = 0; }   // 兜底，别撑爆内存
  };
  src.connect(pr); pr.connect(sink); sink.connect(W.ac.destination);
  W.on = true; W.state = 'idle'; W.n = 0;
  $('#wake').style.background = '#2f4a3a';
  $('#wakeTxt').textContent = '常听中 · 说「小雅小雅」唤醒';
  $('#vst').textContent = '常听模式已开（麦克风常开，识别全部在本机）';
}

function wakeStop(){
  W.on = false;
  try{ W.sp.pr.disconnect(); W.sp.src.disconnect(); W.sp.sink.disconnect(); }catch(e){}
  try{ W.st.getTracks().forEach(t => t.stop()); }catch(e){}
  try{ W.ac.close(); }catch(e){}
  W.sp = null; W.chunks = []; W.state = 'idle';
  $('#wake').style.background = '#23262f';
  $('#wakeTxt').textContent = '常听 · 说「小雅小雅」唤醒';
  $('#lvl').style.width = '0%';
  $('#vst').textContent = '常听模式已关';
}

$('#go').onclick = ()=>ask('civenthra');
$('#cmp').onclick = ()=>ask('cloud');
$('#clr').onclick = ()=>{
  $('#ans').textContent = '等待提问…';
  clear($('#cites')); clear($('#src')); $('#meta').textContent=''; ttsReset();
};
$('#q').addEventListener('keydown', e=>{ if(e.key==='Enter') ask('civenthra'); });
loadInfo();
loadVoice();
</script></body></html>
"""
# 把共享语音核注入主页面（用 replace 而不是 %，因为页面里到处是 % 和 {}）
PAGE = PAGE.replace("/*__VOICE_CORE__*/", VOICE_CORE)


# ── 语音自检页 ──────────────────────────────────────────────────────────────
# 存在的理由有两个，都很实际：
#   ① 用户想知道「我的麦克风到底通不通」——这是排障的第一步，不该靠猜；
#   ② 它和主页面**共用同一份录音代码**（VOICE_CORE），所以无头浏览器跑通它
#      就等于线上那条链路跑通了。两边各写一份的话，自检通过毫无意义。
PAGE_VOICE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>文明穹顶 · 语音自检</title>
<style>
:root{--bg:#0e0f13;--panel:#16181f;--line:#262a35;--fg:#e8e6e3;--dim:#8b90a0;
      --ember:#ff7a18;--ember2:#ffb547;--ok:#3ddc97;--bad:#ff5c5c;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);min-height:100vh;
     font-family:-apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;padding:26px}
h1{font-size:20px;margin:0 0 4px}
h1 span{color:var(--ember)}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
      padding:18px;max-width:820px;margin-bottom:14px}
.step{display:flex;align-items:center;gap:12px;margin-bottom:14px;font-size:14px}
.badge{width:24px;height:24px;border-radius:50%;background:#23262f;border:1px solid var(--line);
       display:flex;align-items:center;justify-content:center;font-size:12px;flex:0 0 auto}
.badge.ok{background:rgba(61,220,151,.15);border-color:var(--ok);color:var(--ok)}
.badge.bad{background:rgba(255,92,92,.15);border-color:var(--bad);color:var(--bad)}
.badge.run{background:rgba(255,181,71,.15);border-color:var(--ember2);color:var(--ember2)}
button{background:linear-gradient(180deg,var(--ember),#e06500);border:0;color:#fff;
       border-radius:10px;padding:10px 20px;font-size:14px;font-weight:600;cursor:pointer;
       font-family:inherit}
button:disabled{opacity:.45;cursor:default}
button.g{background:#23262f;border:1px solid var(--line)}
.lvl{height:8px;border-radius:4px;background:#11131a;overflow:hidden;flex:1;min-width:80px}
.lvl i{display:block;height:100%;width:0;background:linear-gradient(90deg,#3ddc97,#ffb547,#ff5c5c);
       transition:width .06s linear}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:13px}
.heard{background:#11131a;border:1px solid var(--line);border-radius:10px;padding:13px;
       font-size:16px;min-height:44px;margin-top:8px;line-height:1.7}
.kv{color:var(--dim);font-size:12.5px;line-height:1.9}
.kv b{color:var(--ember2);font-weight:600}
a{color:var(--ember2)}
</style></head><body>
<h1>🌐 文明<span>穹顶</span> · 语音自检</h1>
<div class="sub">这个页面跑的是与主页 <b>完全相同</b>的录音/播放代码，用来确认麦克风与扬声器都通。</div>

<div class="card">
  <div class="step"><span class="badge" id="b1">1</span><b>语音服务</b>
    <span class="kv" id="s1">检查中…</span></div>
  <div class="step"><span class="badge" id="b2">2</span><b>麦克风识别</b>
    <span class="kv" id="s2">等待</span></div>
  <div class="step" style="margin-bottom:6px"><span class="badge" id="b3">3</span><b>扬声器朗读</b>
    <span class="kv" id="s3">等待</span></div>
  <div class="step" style="margin-bottom:8px"><span class="lvl"><i id="lvl"></i></span></div>
  <div class="heard" id="heard">—</div>
  <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap">
    <button id="run">▶ 开始自检</button>
    <button id="again" class="g" disabled>只重测麦克风</button>
  </div>
</div>

<div class="card kv" id="info">—</div>
<div class="card kv"><a href="/">← 回到文明穹顶</a></div>

<script>
/*__VOICE_CORE__*/
const $ = s => document.querySelector(s);
function badge(id, cls, txt){ const e = $(id); e.className = 'badge' + (cls?' '+cls:''); if(txt) e.textContent = txt; }
let MIC_ERR = '';                    // 上一次取流失败的具体原因（DOMException.name）
const rec = new Recorder({
  onLevel: v => { $('#lvl').style.width = (v*100).toFixed(0) + '%'; },
  onState: (s, msg) => { if(s === 'rec') badge('#b2','run','…'); else if(s === 'error') MIC_ERR = msg; }
});
const spk = new Speaker({});
let VOICE_OK = false;

/* 排障最怕「只报错、不说下一步」。按错误类型给一句能照着做的建议。 */
function micHint(err){
  if(/NotAllowed|PermissionDenied/.test(err))
    return '浏览器拒绝了麦克风。用 Chrome 打开本页并允许麦克风；内嵌预览面板可能没有麦克风权限。';
  if(/NotFound|DevicesNotFound/.test(err))
    return '系统没检测到麦克风设备。检查 Windows「设置 → 隐私和安全性 → 麦克风」是否已打开、且浏览器在允许列表里。';
  if(/NotReadable|TrackStart/.test(err))
    return '麦克风被别的程序占用了，关掉正在录音的软件（会议、录音工具）再试。';
  if(/Security/.test(err))
    return '页面不在安全上下文。必须用 http://127.0.0.1:8765 打开，不要用 file://。';
  return '用 Chrome 打开 http://127.0.0.1:8765/voicecheck 再试一次。';
}

async function checkInfo(){
  try{
    const d = await fetchVoiceInfo(0, w => { $('#s1').textContent = w.hint || '启动中…'; });
    VOICE_OK = !!d.ok;
    if(VOICE_OK){
      const a = d.info.asr, t = d.info.tts;
      badge('#b1','ok','✓');
      $('#s1').textContent = '就绪 · 识别 ' + String(a.model || '') + ' / ' + String(a.device || '')
        + ' · 朗读 ' + String((t.sapi[0]||'').replace(/^Microsoft\s*/,''));
      $('#info').textContent = '服务地址 ' + String(d.info.url || '') + '\n'
        + 'SAPI 语音：' + t.sapi.map(String).join(' / ') + '\n'
        + 'edge-tts：' + (t.edge ? '可用（需联网）' : '未安装')
        + ' · 模型空闲卸载 ' + (t.idle_unload_s ? (t.idle_unload_s/60)+' 分钟' : '常驻')
        + '\n' + await envLine();
    }else{
      badge('#b1','bad','✕');
      $('#s1').textContent = d.hint || '服务不可用';
      $('#info').textContent = await envLine();
    }
  }catch(e){ badge('#b1','bad','✕'); $('#s1').textContent = '取不到状态：' + e; }
  return VOICE_OK;
}

/* 麦克风能不能用，一半取决于页面环境而不是设备本身。
   把「安全上下文 / mediaDevices / 权限状态」摊开，省得靠猜。 */
async function envLine(){
  let s = '页面安全上下文 ' + (window.isSecureContext ? '是' : '否')
        + ' · mediaDevices ' + (navigator.mediaDevices ? '可用' : '缺失');
  try{
    const p = await navigator.permissions.query({name:'microphone'});
    s += ' · 麦克风权限 ' + ({granted:'已允许', denied:'已拒绝', prompt:'未决定'}[p.state] || p.state);
  }catch(e){ s += ' · 麦克风权限 读取不到'; }
  return s;
}

function testMic(){
  return new Promise(resolve => {
    if(!VOICE_OK){ resolve(false); return; }
    badge('#b2','run','…'); $('#s2').textContent = '正在录音 4 秒，请说话…';
    const r2 = new Recorder({
      onLevel: v => { $('#lvl').style.width = (v*100).toFixed(0) + '%'; },
      onState: (s, msg) => { if(s === 'error') MIC_ERR = msg; },
      onDone: (text, dur, ms, err) => {
        $('#lvl').style.width = '0%';
        if(err){ badge('#b2','bad','✕'); $('#s2').textContent = err; resolve(false); return; }
        if(!text){ badge('#b2','bad','✕'); $('#s2').textContent = '没听清（录到 ' + dur.toFixed(1) + ' 秒，但没识别出内容）';
                   $('#heard').textContent = '（空）'; resolve(false); return; }
        badge('#b2','ok','✓');
        $('#s2').textContent = '识别成功 · 音频 ' + dur.toFixed(1) + 's · 耗时 ' + ms + 'ms';
        $('#heard').textContent = '「' + text + '」';
        resolve(true);
      }
    });
    r2.start({handsFree:false}).then(ok => {
      if(!ok){
        badge('#b2','bad','✕');
        $('#s2').textContent = MIC_ERR || '拿不到麦克风';
        $('#heard').textContent = micHint(MIC_ERR);
        resolve(false); return;
      }
      setTimeout(()=>r2.stop(), 4000);          // 固定录 4 秒，便于无人值守自检
    });
  });
}

async function testSpeaker(){
  badge('#b3','run','…'); $('#s3').textContent = '正在合成并播放…';
  try{
    const t0 = performance.now();
    const r = await fetch('/api/voice/tts', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text:'扬声器测试成功。文明穹顶语音自检完成。', engine:'sapi'})});
    if(!r.ok){ badge('#b3','bad','✕'); $('#s3').textContent = 'HTTP ' + r.status; return false; }
    const blob = await r.blob();
    const ms = Math.round(performance.now() - t0);
    badge('#b3','ok','✓');
    $('#s3').textContent = '已播放 · ' + Math.round(blob.size/1024) + ' KB · 合成耗时 ' + ms + 'ms · 引擎 '
      + (r.headers.get('X-Voice-Engine')||'');
    await playBlob(blob, {audio:null, resolve:null});
    return true;
  }catch(e){ badge('#b3','bad','✕'); $('#s3').textContent = '失败：' + e; return false; }
}

async function runAll(){
  $('#run').disabled = true;
  const a = await checkInfo();
  let b = false, c = false;
  if(a){ b = await testMic(); c = await testSpeaker(); }
  $('#again').disabled = !a;
  $('#run').disabled = false;
  document.title = (a && b && c) ? '语音自检 · 全部通过 ✅' : '语音自检 · 有问题 ⚠';
}

$('#run').onclick = runAll;
$('#again').onclick = testMic;
/* 无人值守自检：URL 带 ?auto=1 时自动开跑（无头浏览器验证用） */
if(location.search.indexOf('auto=1') >= 0) setTimeout(runAll, 300);
</script></body></html>
"""
PAGE_VOICE = PAGE_VOICE.replace("/*__VOICE_CORE__*/", VOICE_CORE)


def render_page(template: str, token: str, nonce: str, diagnostics: bool) -> str:
    """Inject per-run values without allowing them to become executable markup."""
    return (
        template.replace("/*__SESSION_TOKEN__*/", json.dumps(token))
        .replace("/*__VOICE_DIAGNOSTICS__*/", "true" if diagnostics else "false")
        .replace("<script>", '<script nonce="%s">' % nonce)
    )


_voice_starting = {"flag": False}
_voice_lock = threading.Lock()


def _voice_info() -> dict:
    """语音服务状态。不在线就**在后台**把它拉起来 —— 不能让页面加载卡在这里等着。

    注意首次 ASR 还要载 whisper 模型（约 4 秒），所以「就绪」指的是服务在，
    不是模型已进显存；模型是第一次识别时才懒加载的。
    """
    cl = voice.client()
    info = cl.info(refresh=True)
    if info:
        return {"ok": True, "info": info}
    if voice.C.VOICE_AUTOSTART:
        with _voice_lock:
            if not _voice_starting["flag"]:
                _voice_starting["flag"] = True

                def _go():
                    try:
                        cl.ensure()
                    finally:
                        with _voice_lock:
                            _voice_starting["flag"] = False

                threading.Thread(target=_go, daemon=True, name="voice-boot").start()
        return {"ok": False, "starting": True,
                "hint": "语音服务启动中，几秒后自动重试…"}
    return {"ok": False, "hint": "语音服务未启动；请从本主程序重新启用语音功能"}


class Handler(BaseHTTPRequestHandler):
    kb: Civenthra = None
    session_token = ""
    csp_nonce = ""
    protocol_version = "HTTP/1.1"
    server_version = "Civenthra"
    sys_version = ""
    expensive_slots = threading.BoundedSemaphore(2)
    voice_slots = threading.BoundedSemaphore(2)
    diagnostic_lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _common_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "microphone=(self)")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _raw(self, body: bytes, mime: str, extra: dict = None):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit: int) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            self._json({"error": "chunked request bodies are not supported"}, 400)
            raise RequestRejected
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json({"error": "invalid content length"}, 400)
            raise RequestRejected
        if n < 0:
            self._json({"error": "invalid content length"}, 400)
            raise RequestRejected
        if n > limit:
            self.close_connection = True
            self._json({"error": "request body too large"}, 413)
            raise RequestRejected
        body = self.rfile.read(n) if n else b""
        if len(body) != n:
            self._json({"error": "incomplete request body"}, 400)
            raise RequestRejected
        return body

    def _content_type(self) -> str:
        return self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()

    def _require_content_type(self, expected: str):
        if self._content_type() != expected:
            self._json({"error": "unsupported content type"}, 415)
            raise RequestRejected

    def _validate_request(self, require_token: bool):
        remote = self.client_address[0] if self.client_address else None
        host = self.headers.get("Host")
        if (not is_loopback_host(remote)
                or not host_header_is_local(host, self.server.server_port)):
            self._json({"error": "local request required"}, 403)
            raise RequestRejected
        if not origin_matches(self.headers.get("Origin"), host):
            self._json({"error": "cross-origin request rejected"}, 403)
            raise RequestRejected
        if require_token and not token_matches(
                self.headers.get(TOKEN_HEADER), self.session_token):
            self._json({"error": "invalid session token"}, 403)
            raise RequestRejected

    def _html(self, text: str):
        body = render_page(
            text, self.session_token, self.csp_nonce, C.VOICE_DIAGNOSTICS
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'nonce-%s'; style-src 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'" % self.csp_nonce,
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            self._validate_request(require_token=p.startswith("/api/"))
        except RequestRejected:
            return
        if p in ("/", "/index.html"):
            self._html(PAGE)
        elif p in ("/voicecheck", "/voicecheck/"):
            self._html(PAGE_VOICE)
        elif p == "/api/info":
            ix = self.kb.index
            self._json({"chunks": ix.n_chunks, "books": len(ix.books()),
                        "dense": ix.dense is not None,
                        "airgap": airgap.engaged(),
                        "graph": self.kb.graph is not None, "graph_mode": self.kb.graph_mode})
        elif p.startswith("/api/graph/"):
            try:
                graph = self.kb.graph
                if graph is None:
                    return self._json({"available": False, "fallback": "baseline"}, 503)
                if p == "/api/graph/status":
                    return self._json(dict(graph.status(), available=True))
                if p == "/api/graph/claims":
                    return self._json({"claims": graph.claims()})
                if p == "/api/graph/evidence":
                    from .evidence import verify
                    pid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                    passage = graph.passage(pid)
                    if not passage:
                        return self._json({"error": "Unknown evidence ID"}, 404)
                    return self._json(dict(passage, verified=verify(passage)))
                return self._json({"error": "not found"}, 404)
            except (OSError, ValueError, TypeError, sqlite3.Error):
                self._json({"available": False, "fallback": "baseline"}, 503)
        elif p == "/api/voice/info":
            self._json(_voice_info())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            self._validate_request(require_token=True)
            if path == "/api/voice/asr":
                self._require_content_type("application/octet-stream")
                return self._voice_asr()
            if path == "/api/voice/tts":
                self._require_content_type("application/json")
                return self._voice_tts()
            if path == "/api/voice/report":
                self._require_content_type("application/json")
                return self._voice_report()
            if path != "/api/ask":
                return self._json({"error": "not found"}, 404)
            self._require_content_type("application/json")
            req = json.loads(self._body(MAX_JSON_BODY).decode("utf-8"))
        except RequestRejected:
            return
        except (UnicodeError, json.JSONDecodeError):
            return self._json({"error": "bad json"}, 400)

        question = req.get("question") if isinstance(req, dict) else None
        mode = req.get("mode", "civenthra") if isinstance(req, dict) else None
        language = req.get("language", "auto") if isinstance(req, dict) else None
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            return self._json({"error": "invalid question"}, 400)
        if mode not in {"civenthra", "cloud"} or language not in {"auto", "en", "zh-CN"}:
            return self._json({"error": "invalid option"}, 400)
        if not self.expensive_slots.acquire(blocking=False):
            return self._json({"error": "busy"}, 429)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._common_headers()
        self.end_headers()
        self.close_connection = True

        def send(obj):
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        try:
            for ev in self.kb.stream_ask(
                    question, mode=mode, language=language):
                send(ev)
        except BrokenPipeError:
            pass
        except Exception as exc:
            request_id = secrets.token_hex(4)
            print("  [serve:%s] ask failed: %s" % (request_id, exc), file=sys.stderr)
            send({"type": "delta", "text": "\n[请求失败，编号 %s]" % request_id})
            send({"type": "done"})
        finally:
            self.expensive_slots.release()

    # ── 语音 ──────────────────────────────────────────────────────────
    def _voice_asr(self):
        if not self.voice_slots.acquire(blocking=False):
            return self._json({"error": "busy"}, 429)
        try:
            pcm = self._body(MAX_PCM_BODY)
            try:
                sr = int(self.headers.get("X-Sample-Rate") or 16000)
            except ValueError:
                return self._json({"error": "invalid sample rate"}, 400)
            if not 8000 <= sr <= 192000:
                return self._json({"error": "invalid sample rate"}, 400)
            out = voice.client().asr(pcm, sr)
            if out is None:
                return self._json({"error": "语音服务不可用"}, 503)
            self._json(out)
        finally:
            self.voice_slots.release()

    def _voice_tts(self):
        if not self.voice_slots.acquire(blocking=False):
            return self._json({"error": "busy"}, 429)
        try:
            try:
                req = json.loads(self._body(MAX_JSON_BODY).decode("utf-8") or "{}")
            except (UnicodeError, json.JSONDecodeError):
                return self._json({"error": "bad json"}, 400)
            text = req.get("text", "") if isinstance(req, dict) else ""
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                return self._json({"error": "invalid text"}, 400)
            got = voice.client().tts(text, req.get("engine"),
                                     req.get("voice"), req.get("rate"))
            if got is None:
                return self._json({"error": "语音服务不可用"}, 503)
            data, mime, used = got
            self._raw(data, mime, {"X-Voice-Engine": used})
        finally:
            self.voice_slots.release()

    def _voice_report(self):
        """浏览器把「它那一侧录到了什么」写进 MIC_REPORT.jsonl。

        没有这个口，服务端只看得见「收到 N 字节」或「识别出空串」，
        分不清是**没录到声音**还是**录到了但识别不出来**（修法相反）。
        """
        if not C.VOICE_DIAGNOSTICS:
            return self._json({"error": "voice diagnostics disabled"}, 404)
        try:
            incoming = json.loads(self._body(MAX_REPORT_BODY).decode("utf-8") or "{}")
        except (UnicodeError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "bad json"}, 400)
        if not isinstance(incoming, dict):
            return self._json({"ok": False, "error": "bad json"}, 400)
        allowed = {"where", "why", "dur", "bytes", "sr", "maxLevel",
                   "textLength", "err", "ms"}
        rec = {key: incoming[key] for key in allowed if key in incoming}
        if C.VOICE_DIAGNOSTICS_INCLUDE_TEXT and isinstance(incoming.get("text"), str):
            rec["text"] = incoming["text"][:1000]
        if isinstance(rec.get("err"), str):
            rec["err"] = rec["err"][:300]
        rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.diagnostic_lock:
                if (C.MIC_REPORT.exists()
                        and C.MIC_REPORT.stat().st_size >= C.MIC_REPORT_MAX_BYTES):
                    return self._json({"ok": False, "error": "diagnostic log full"}, 507)
                with open(C.MIC_REPORT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            print("  [serve] voice diagnostic write failed: %s" % exc, file=sys.stderr)
            return self._json({"ok": False, "error": "diagnostic write failed"}, 500)
        return self._json({"ok": True})


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="civenthra.serve")
    ap.add_argument("--port", type=int, default=C.WEB_PORT)
    ap.add_argument("--model", default=None)
    ap.add_argument("--small", action="store_true")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--no-airgap", action="store_true")
    ap.add_argument("--no-dense", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args(argv)

    Handler.session_token = new_session_token()
    Handler.csp_nonce = new_session_token()
    os.environ[TOKEN_ENV] = Handler.session_token

    if not a.no_airgap:
        airgap.engage()
        ok = airgap.verify()
        print(airgap.banner())
        print("  进程网络钩子：%s" % ("检查通过" if ok else "⚠ 检查失败"))

    server = LlamaServer(model=a.model or (C.MODEL_SMALL if a.small else C.MODEL_MAIN),
                         ngl=(0 if a.cpu else C.NGL))
    print("  加载模型 %s …" % server.model.name)
    server.start()
    es = None
    if not a.no_dense:
        try:
            es = EmbedServer()
            es.start()
        except Exception as e:
            print("  （向量服务不可用，退化为 BM25：%s）" % str(e)[:70])
            es = None
    kb = Civenthra(server=server)
    if es:
        kb.index = IndexBundle.load(C.INDEX_DIR, dense=True, embed_server=es)
    Handler.kb = kb
    print("  索引 %d 块 / %d 本" % (kb.index.n_chunks, len(kb.index.books())))

    if a.no_voice:
        print("  语音：已关闭（--no-voice）")
    else:
        cl = voice.client()
        if cl.ping():
            print("  语音：就绪（%s）" % cl.info(refresh=True).get("asr", {}).get("model", "?"))
        else:
            print("  语音：后台拉起中（首次识别还要载模型，约 4 秒）")

    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    httpd.daemon_threads = True
    print("\n  🔥 打开： http://127.0.0.1:%d\n" % a.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        if es:
            es.stop()


if __name__ == "__main__":
    main()
