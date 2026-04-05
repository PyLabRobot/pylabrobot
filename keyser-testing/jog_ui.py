"""Web-based jog UI for Tecan EVO.

Flask server with real-time position display and keyboard-driven jogging
for both LiHa and RoMa arms.

Numpad controls:
  LiHa:
    4/6  X left/right
    8/2  Y forward/back
    +/-  Z up/down (away from / toward deck)
    7/9  Step size down/up

  RoMa:
    Arrow keys: X left/right, Y forward/back
    PageUp/PageDown: Z up/down
    Home/End: R rotate

Usage:
  python keyser-testing/jog_ui.py
  Then open http://localhost:5050
"""

import asyncio
import json
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, render_template_string, request

logging.basicConfig(level=logging.WARNING)

# Globals for the EVO connection
evo = None
driver = None
loop = None

STEP_SIZES = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
current_step_idx = 4  # start at 5mm
current_arm = "liha"

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "taught_positions.json")
LABWARE_FILE = os.path.join(os.path.dirname(__file__), "labware_edits.json")


def run_async(coro):
  """Run an async coroutine from sync Flask context."""
  future = asyncio.run_coroutine_threadsafe(coro, loop)
  return future.result(timeout=30)


app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Tecan EVO Jog</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Tahoma, sans-serif; background: #1a1a2e; color: #eee;
         display: flex; flex-direction: column; height: 100vh; }
  .header { background: #16213e; padding: 12px 20px; display: flex; justify-content: space-between;
            align-items: center; border-bottom: 2px solid #0f3460; }
  .header h1 { font-size: 18px; color: #e94560; }
  .status { font-size: 12px; color: #888; }
  .main { display: flex; flex: 1; overflow: hidden; }

  .panel { padding: 16px; overflow-y: auto; }
  .left { width: 55%; border-right: 1px solid #333; }
  .right { width: 45%; }

  .position-box { background: #16213e; border-radius: 8px; padding: 16px; margin-bottom: 12px;
                  border: 1px solid #0f3460; }
  .position-box h2 { font-size: 14px; color: #e94560; margin-bottom: 10px; text-transform: uppercase;
                     letter-spacing: 1px; }
  .position-box.active { border-color: #e94560; box-shadow: 0 0 10px rgba(233,69,96,0.3); }

  .pos-grid { display: grid; grid-template-columns: 60px 1fr 80px; gap: 4px; align-items: center; }
  .pos-label { font-weight: bold; color: #aaa; font-size: 13px; }
  .pos-bar { background: #0a0a1a; border-radius: 4px; height: 24px; position: relative; overflow: hidden; }
  .pos-fill { height: 100%; background: linear-gradient(90deg, #0f3460, #e94560); border-radius: 4px;
              transition: width 0.3s; }
  .pos-value { font-family: 'Courier New', monospace; font-size: 14px; text-align: right; }

  .controls { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  .btn { padding: 6px 14px; border: 1px solid #444; background: #16213e; color: #eee;
         border-radius: 4px; cursor: pointer; font-size: 12px; }
  .btn:hover { background: #0f3460; }
  .btn.active { background: #e94560; border-color: #e94560; }
  .btn.small { padding: 4px 8px; font-size: 11px; }

  .step-display { background: #0a0a1a; padding: 8px 16px; border-radius: 4px;
                  font-family: monospace; font-size: 16px; text-align: center;
                  color: #e94560; margin-bottom: 12px; }

  .key-help { font-size: 11px; color: #666; line-height: 1.8; }
  .key { display: inline-block; background: #333; padding: 2px 6px; border-radius: 3px;
         font-family: monospace; font-size: 11px; min-width: 20px; text-align: center;
         border: 1px solid #555; }

  .teach-section { margin-top: 12px; }
  .teach-row { display: flex; gap: 8px; margin-bottom: 6px; align-items: center; }
  .teach-row input { background: #0a0a1a; border: 1px solid #444; color: #eee; padding: 4px 8px;
                     border-radius: 4px; font-size: 12px; width: 120px; }
  .teach-row select { background: #0a0a1a; border: 1px solid #444; color: #eee; padding: 4px 8px;
                      border-radius: 4px; font-size: 12px; }

  .log { background: #0a0a1a; border-radius: 4px; padding: 8px; font-family: monospace;
         font-size: 11px; max-height: 200px; overflow-y: auto; color: #888; }
  .log .entry { margin-bottom: 2px; }
  .log .ok { color: #4ec9b0; }
  .log .err { color: #e94560; }

  .saved-positions { margin-top: 12px; }
  .saved-pos { display: flex; justify-content: space-between; align-items: center;
               padding: 4px 8px; background: #16213e; border-radius: 4px; margin-bottom: 4px;
               font-size: 12px; }
</style>
</head>
<body>
<div class="header">
  <h1>Tecan EVO Jog & Teach</h1>
  <div style="display:flex;align-items:center;gap:12px;">
    <button class="btn" id="btn-connect" onclick="toggleConnect()" style="background:#0f3460">Connect</button>
    <div class="status" id="status">Disconnected</div>
  </div>
</div>
<div class="main">
  <div class="panel left">
    <!-- Arm selector -->
    <div class="controls">
      <button class="btn active" id="btn-liha" onclick="setArm('liha')">LiHa (Pipette)</button>
      <button class="btn" id="btn-roma" onclick="setArm('roma')">RoMa (Plate)</button>
    </div>

    <!-- Step size -->
    <div class="step-display">
      Step: <span id="step-size">5.0</span> mm
      <span style="font-size:11px;color:#666;margin-left:8px">
        (<span class="key">7</span> smaller / <span class="key">9</span> bigger)
      </span>
    </div>

    <!-- LiHa position -->
    <div class="position-box active" id="box-liha">
      <h2>LiHa Position</h2>
      <div class="pos-grid">
        <span class="pos-label">X</span>
        <div class="pos-bar"><div class="pos-fill" id="liha-x-bar" style="width:50%"></div></div>
        <span class="pos-value" id="liha-x">—</span>

        <span class="pos-label">Y</span>
        <div class="pos-bar"><div class="pos-fill" id="liha-y-bar" style="width:50%"></div></div>
        <span class="pos-value" id="liha-y">—</span>

        <span class="pos-label">Z1</span>
        <div class="pos-bar"><div class="pos-fill" id="liha-z-bar" style="width:50%"></div></div>
        <span class="pos-value" id="liha-z">—</span>
      </div>
    </div>

    <!-- RoMa position -->
    <div class="position-box" id="box-roma">
      <h2>RoMa Position</h2>
      <div class="pos-grid">
        <span class="pos-label">X</span>
        <div class="pos-bar"><div class="pos-fill" id="roma-x-bar" style="width:50%"></div></div>
        <span class="pos-value" id="roma-x">—</span>

        <span class="pos-label">Y</span>
        <div class="pos-bar"><div class="pos-fill" id="roma-y-bar" style="width:50%"></div></div>
        <span class="pos-value" id="roma-y">—</span>

        <span class="pos-label">Z</span>
        <div class="pos-bar"><div class="pos-fill" id="roma-z-bar" style="width:50%"></div></div>
        <span class="pos-value" id="roma-z">—</span>

        <span class="pos-label">R</span>
        <div class="pos-bar"><div class="pos-fill" id="roma-r-bar" style="width:50%"></div></div>
        <span class="pos-value" id="roma-r">—</span>
      </div>
    </div>

    <!-- Key help -->
    <div class="key-help">
      <b>LiHa (Numpad):</b>
      <span class="key">4</span>/<span class="key">6</span> X &nbsp;
      <span class="key">8</span>/<span class="key">2</span> Y &nbsp;
      <span class="key">+</span>/<span class="key">-</span> Z up/down &nbsp;
      <span class="key">7</span>/<span class="key">9</span> Step
      <br>
      <b>RoMa (Arrows):</b>
      <span class="key">←</span>/<span class="key">→</span> X &nbsp;
      <span class="key">↑</span>/<span class="key">↓</span> Y &nbsp;
      <span class="key">PgUp</span>/<span class="key">PgDn</span> Z &nbsp;
      <span class="key">Home</span>/<span class="key">End</span> R
    </div>
  </div>

  <div class="panel right">
    <!-- Labware Inspector -->
    <div>
      <h2 style="font-size:14px;color:#e94560;margin-bottom:8px;">LABWARE</h2>
      <div class="controls" id="labware-tabs"></div>
      <div id="labware-detail" style="background:#16213e;border-radius:8px;padding:12px;
           border:1px solid #0f3460;font-size:12px;margin-bottom:12px;">
        <i style="color:#666">Select labware above</i>
      </div>
    </div>

    <!-- Teach -->
    <div>
      <h2 style="font-size:14px;color:#e94560;margin-bottom:8px;">TEACH FROM CURRENT Z</h2>
      <div class="teach-row">
        <select id="teach-field" style="width:110px">
          <option value="z_start">z_start</option>
          <option value="z_dispense">z_dispense</option>
          <option value="z_max">z_max</option>
        </select>
        <select id="teach-labware"></select>
        <button class="btn" onclick="teachLabware()">Set</button>
      </div>
      <div class="teach-row" style="margin-top:6px">
        <input type="text" id="teach-label" placeholder="Label (e.g. tip_top)" style="width:160px">
        <button class="btn" onclick="recordPosition()">Record Position</button>
      </div>
    </div>

    <!-- Quick actions -->
    <div style="margin-top:12px;">
      <h2 style="font-size:14px;color:#e94560;margin-bottom:8px;">ACTIONS</h2>
      <div class="controls">
        <button class="btn" onclick="sendAction('home')">Home LiHa</button>
        <button class="btn" onclick="sendAction('park_roma')">Park RoMa</button>
        <button class="btn" onclick="sendAction('tips_status')">Check Tips</button>
        <button class="btn" onclick="sendAction('ree')">Axis Status</button>
      </div>
      <details style="margin-top:4px">
        <summary style="cursor:pointer;font-size:11px;color:#666">Lamp & Power Controls</summary>
        <div class="controls" style="margin-top:4px">
          <button class="btn small" onclick="sendAction('lamp_green')" style="border-color:#4ec9b0">Lamp Green</button>
          <button class="btn small" onclick="sendAction('lamp_off')">Lamp Off</button>
          <button class="btn small" onclick="sendAction('lamp_test')">Lamp Test</button>
          <button class="btn small" onclick="sendAction('power_on')" style="border-color:#e9c46a">Motor Power</button>
          <button class="btn small" onclick="sendAction('power_off')">Power Off</button>
        </div>
      </details>
    </div>

    <!-- Saved positions -->
    <div class="saved-positions" style="margin-top:12px;">
      <h2 style="font-size:14px;color:#e94560;margin-bottom:8px;">SAVED POSITIONS</h2>
      <div id="saved-list"></div>
    </div>

    <!-- Log -->
    <div style="margin-top:12px;">
      <h2 style="font-size:14px;color:#e94560;margin-bottom:8px;">LOG</h2>
      <div class="log" id="log"></div>
    </div>
  </div>
</div>

<script>
let arm = 'liha';
let stepIdx = 4;
const STEPS = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0];
let polling = null;

function setArm(a) {
  arm = a;
  document.getElementById('btn-liha').className = 'btn' + (a==='liha' ? ' active' : '');
  document.getElementById('btn-roma').className = 'btn' + (a==='roma' ? ' active' : '');
  document.getElementById('box-liha').className = 'position-box' + (a==='liha' ? ' active' : '');
  document.getElementById('box-roma').className = 'position-box' + (a==='roma' ? ' active' : '');
}

function updateStep() {
  document.getElementById('step-size').textContent = STEPS[stepIdx].toFixed(1);
}

function log(msg, cls) {
  const el = document.getElementById('log');
  const entry = document.createElement('div');
  entry.className = 'entry ' + (cls || '');
  entry.textContent = msg;
  el.appendChild(entry);
  el.scrollTop = el.scrollHeight;
}

let busy = false;

async function sendJog(armName, axis, direction) {
  if (!isConnected) { log('Not connected — click Connect first', 'err'); return; }
  if (busy) { log('Busy — wait for move to finish', 'err'); return; }
  busy = true;
  document.getElementById('status').textContent = 'Moving...';
  document.getElementById('status').style.color = '#e9c46a';
  const cmd = armName.toUpperCase() + ' ' + axis + (direction > 0 ? '+' : '-') + ' ' + STEPS[stepIdx] + 'mm';
  log('> ' + cmd, '');
  try {
    const resp = await fetch('/jog', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({arm: armName, axis: axis, direction: direction, step: STEPS[stepIdx]})
    });
    const data = await resp.json();
    if (data.error) { log('  ERR: ' + data.error, 'err'); }
    else {
      updatePositions(data);
      if (data.cmd) { log('  ' + data.cmd, 'ok'); }
    }
  } catch(e) { log('  Failed: ' + e, 'err'); }
  finally {
    busy = false;
    document.getElementById('status').textContent = 'Connected';
    document.getElementById('status').style.color = '#4ec9b0';
  }
}

async function sendAction(action) {
  if (busy) { log('Busy — wait for current operation', 'err'); return; }
  busy = true;
  document.getElementById('status').textContent = 'Busy...';
  document.getElementById('status').style.color = '#e9c46a';
  log('> ACTION: ' + action, '');
  try {
    const resp = await fetch('/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: action})
    });
    const data = await resp.json();
    if (data.message) log('  ' + data.message, 'ok');
    if (data.error) log('  ' + data.error, 'err');
    updatePositions(data);
    if (data.saved) loadSaved();
  } catch(e) { log('  Action failed: ' + e, 'err'); }
  finally {
    busy = false;
    document.getElementById('status').textContent = 'Connected';
    document.getElementById('status').style.color = '#4ec9b0';
  }
}

async function recordPosition() {
  const label = document.getElementById('teach-label').value.trim();
  if (!label) { log('Enter a label first', 'err'); return; }
  log('> RECORD: ' + label, '');
  try {
    const resp = await fetch('/record', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({label: label})
    });
    const data = await resp.json();
    log(data.message || data.error, data.error ? 'err' : 'ok');
    loadSaved();
  } catch(e) { log('Record failed: ' + e, 'err'); }
}

async function teachLabware() {
  const field = document.getElementById('teach-field').value;
  const labware = document.getElementById('teach-labware').value;
  log('> TEACH: ' + labware + '.' + field + ' = current Z', '');
  try {
    const resp = await fetch('/teach', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({field: field, labware: labware})
    });
    const data = await resp.json();
    log('  ' + (data.message || data.error), data.error ? 'err' : 'ok');
    loadLabware();  // refresh to show EDITED tag
  } catch(e) { log('  Teach failed: ' + e, 'err'); }
}

function updatePositions(data) {
  if (data.liha) {
    document.getElementById('liha-x').textContent = (data.liha.x/10).toFixed(1) + ' mm';
    document.getElementById('liha-y').textContent = (data.liha.y/10).toFixed(1) + ' mm';
    document.getElementById('liha-z').textContent = (data.liha.z/10).toFixed(1) + ' mm';
    document.getElementById('liha-x-bar').style.width = Math.min(100, data.liha.x/100) + '%';
    document.getElementById('liha-y-bar').style.width = Math.min(100, data.liha.y/30) + '%';
    document.getElementById('liha-z-bar').style.width = Math.min(100, data.liha.z/21) + '%';
  }
  if (data.roma) {
    document.getElementById('roma-x').textContent = (data.roma.x/10).toFixed(1) + ' mm';
    document.getElementById('roma-y').textContent = (data.roma.y/10).toFixed(1) + ' mm';
    document.getElementById('roma-z').textContent = (data.roma.z/10).toFixed(1) + ' mm';
    document.getElementById('roma-r').textContent = (data.roma.r/10).toFixed(1) + ' deg';
    document.getElementById('roma-x-bar').style.width = Math.min(100, data.roma.x/100) + '%';
    document.getElementById('roma-y-bar').style.width = Math.min(100, data.roma.y/30) + '%';
    document.getElementById('roma-z-bar').style.width = Math.min(100, data.roma.z/26) + '%';
    document.getElementById('roma-r-bar').style.width = Math.min(100, data.roma.r/36) + '%';
  }
}

async function pollPositions() {
  if (busy || !isConnected) return;
  try {
    const resp = await fetch('/positions');
    if (busy || !isConnected) return;
    const data = await resp.json();
    if (busy || !isConnected) return;
    updatePositions(data);
  } catch(e) {
    document.getElementById('status').textContent = 'Disconnected';
    document.getElementById('status').style.color = '#e94560';
  }
}

async function loadSaved() {
  try {
    const resp = await fetch('/saved');
    const data = await resp.json();
    const list = document.getElementById('saved-list');
    list.innerHTML = '';
    for (const [label, pos] of Object.entries(data)) {
      const div = document.createElement('div');
      div.className = 'saved-pos';
      div.innerHTML = '<span>' + label + '</span><span style="color:#888">X=' +
        pos.x + ' Y=' + pos.y + ' Z1=' + (pos.z ? pos.z[0] : '?') + '</span>';
      list.appendChild(div);
    }
  } catch(e) {}
}

// Keyboard handler
document.addEventListener('keydown', function(e) {
  // Prevent page scrolling for arrow keys
  if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','PageUp','PageDown','Home','End'].includes(e.key)) {
    e.preventDefault();
  }

  // LiHa numpad
  if (e.key === '4' || e.code === 'Numpad4') { sendJog('liha', 'x', -1); return; }
  if (e.key === '6' || e.code === 'Numpad6') { sendJog('liha', 'x', 1); return; }
  if (e.key === '8' || e.code === 'Numpad8') { sendJog('liha', 'y', 1); return; }
  if (e.key === '2' || e.code === 'Numpad2') { sendJog('liha', 'y', -1); return; }
  if (e.code === 'NumpadAdd' || (e.key === '+' && !e.target.tagName.match(/INPUT/i))) {
    sendJog('liha', 'z', 1); return; }   // + = up (away from deck, increase Z)
  if (e.code === 'NumpadSubtract' || (e.key === '-' && !e.target.tagName.match(/INPUT/i))) {
    sendJog('liha', 'z', -1); return; }  // - = down (toward deck, decrease Z)
  if (e.key === '7' || e.code === 'Numpad7') {
    stepIdx = Math.max(0, stepIdx - 1); updateStep(); return; }
  if (e.key === '9' || e.code === 'Numpad9') {
    stepIdx = Math.min(STEPS.length - 1, stepIdx + 1); updateStep(); return; }

  // RoMa arrows
  if (e.key === 'ArrowLeft') { sendJog('roma', 'x', -1); return; }
  if (e.key === 'ArrowRight') { sendJog('roma', 'x', 1); return; }
  if (e.key === 'ArrowUp') { sendJog('roma', 'y', 1); return; }
  if (e.key === 'ArrowDown') { sendJog('roma', 'y', -1); return; }
  if (e.key === 'PageUp') { sendJog('roma', 'z', 1); return; }   // up
  if (e.key === 'PageDown') { sendJog('roma', 'z', -1); return; } // down
  if (e.key === 'Home') { sendJog('roma', 'r', -1); return; }
  if (e.key === 'End') { sendJog('roma', 'r', 1); return; }
});

let isConnected = false;

async function toggleConnect() {
  const btn = document.getElementById('btn-connect');
  const status = document.getElementById('status');
  if (isConnected) {
    btn.textContent = 'Disconnecting...';
    btn.disabled = true;
    try {
      const resp = await fetch('/disconnect', {method: 'POST'});
      const data = await resp.json();
      isConnected = data.connected;
    } catch(e) { log('Disconnect failed: ' + e, 'err'); }
  } else {
    btn.textContent = 'Connecting...';
    btn.disabled = true;
    status.textContent = 'Connecting...';
    status.style.color = '#e9c46a';
    log('> Connecting to EVO...', '');
    try {
      const resp = await fetch('/connect', {method: 'POST'});
      const data = await resp.json();
      isConnected = data.connected;
      if (data.message) log('  ' + data.message, 'ok');
      if (data.error) log('  ' + data.error, 'err');
    } catch(e) { log('  Connect failed: ' + e, 'err'); }
  }
  updateConnectButton();
}

function updateConnectButton() {
  const btn = document.getElementById('btn-connect');
  const status = document.getElementById('status');
  btn.disabled = false;
  if (isConnected) {
    btn.textContent = 'Disconnect';
    btn.style.background = '#e94560';
    status.textContent = 'Connected';
    status.style.color = '#4ec9b0';
  } else {
    btn.textContent = 'Connect';
    btn.style.background = '#0f3460';
    status.textContent = 'Disconnected';
    status.style.color = '#888';
  }
}

let labwareData = {};

async function loadLabware() {
  try {
    const resp = await fetch('/labware');
    labwareData = await resp.json();
    const tabs = document.getElementById('labware-tabs');
    const select = document.getElementById('teach-labware');
    tabs.innerHTML = '';
    select.innerHTML = '';
    for (const name of Object.keys(labwareData)) {
      const btn = document.createElement('button');
      btn.className = 'btn small';
      btn.textContent = name;
      btn.onclick = () => showLabware(name);
      tabs.appendChild(btn);
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
    // Show first by default
    const first = Object.keys(labwareData)[0];
    if (first) showLabware(first);
  } catch(e) { log('Failed to load labware: ' + e, 'err'); }
}

function showLabware(name) {
  const lw = labwareData[name];
  if (!lw) return;
  // Highlight active tab
  document.querySelectorAll('#labware-tabs .btn').forEach(b => {
    b.className = 'btn small' + (b.textContent === name ? ' active' : '');
  });
  // Also set the teach dropdown
  document.getElementById('teach-labware').value = name;

  let html = '<div style="margin-bottom:8px">';
  html += '<b style="color:#e94560;font-size:13px">' + name + '</b>';
  html += '<span style="color:#666;margin-left:8px">' + lw.type + '</span>';
  html += '</div>';
  html += '<div style="color:#888;margin-bottom:6px">' + lw.model + '</div>';
  html += '<table style="width:100%;font-family:monospace;font-size:11px;border-collapse:collapse">';

  const rows = [
    ['Size', lw.size_x + ' x ' + lw.size_y + ' x ' + lw.size_z + ' mm'],
    ['Location (deck)', 'x=' + lw.loc_x + '  y=' + lw.loc_y + '  z=' + lw.loc_z + ' mm'],
  ];
  if (lw.z_start !== undefined) rows.push(['z_start', lw.z_start + ' (' + (lw.z_start/10).toFixed(1) + 'mm)' + (lw.edited_z_start ? ' <span style="color:#e9c46a">EDITED</span>' : '')]);
  if (lw.z_dispense !== undefined) rows.push(['z_dispense', lw.z_dispense + ' (' + (lw.z_dispense/10).toFixed(1) + 'mm)' + (lw.edited_z_dispense ? ' <span style="color:#e9c46a">EDITED</span>' : '')]);
  if (lw.z_max !== undefined) rows.push(['z_max', lw.z_max + ' (' + (lw.z_max/10).toFixed(1) + 'mm)' + (lw.edited_z_max ? ' <span style="color:#e9c46a">EDITED</span>' : '')]);
  if (lw.area !== undefined) rows.push(['area', lw.area + ' mm²']);
  if (lw.item_dy !== undefined) rows.push(['well pitch', lw.item_dy + ' mm']);
  if (lw.num_items !== undefined) rows.push(['wells/tips', lw.num_items + ' (' + lw.num_items_x + 'x' + lw.num_items_y + ')']);
  if (lw.tip_length !== undefined) rows.push(['tip length', lw.tip_length + ' mm']);
  if (lw.tip_type !== undefined) rows.push(['tip type', lw.tip_type]);

  for (const [label, val] of rows) {
    html += '<tr><td style="padding:2px 8px 2px 0;color:#aaa;white-space:nowrap">' + label + '</td>';
    html += '<td style="padding:2px 0">' + val + '</td></tr>';
  }
  html += '</table>';
  document.getElementById('labware-detail').innerHTML = html;
}

// Start polling
polling = setInterval(pollPositions, 1000);
pollPositions();
loadSaved();
loadLabware();
</script>
</body>
</html>
"""


import threading

_usb_lock = threading.Lock()


@app.route("/")
def index():
  return render_template_string(HTML)


@app.route("/positions")
def positions():
  if not connected:
    return jsonify({})
  if not _usb_lock.acquire(blocking=False):
    return jsonify({})
  try:
    liha_pos = run_async(get_liha_position())
    roma_pos = run_async(get_roma_position())
    return jsonify({"liha": liha_pos, "roma": roma_pos})
  except Exception as e:
    return jsonify({"error": str(e)})
  finally:
    _usb_lock.release()


@app.route("/jog", methods=["POST"])
def jog():
  data = request.json
  arm_name = data["arm"]
  axis = data["axis"]
  direction = data["direction"]
  step_mm = data["step"]
  delta = int(step_mm * 10 * direction)

  module = "C5" if arm_name == "liha" else "C1"
  cmd_map = {"x": "PRX", "y": "PRY", "z": "PRZ", "r": "PRR"}
  cmd_name = f"{module} {cmd_map.get(axis, '?')}{delta}"

  with _usb_lock:
    try:
      run_async(do_jog(arm_name, axis, delta))
      liha_pos = run_async(get_liha_position())
      roma_pos = run_async(get_roma_position())
      return jsonify({"liha": liha_pos, "roma": roma_pos, "cmd": cmd_name})
    except Exception as e:
      return jsonify({"error": str(e), "cmd": cmd_name})


@app.route("/record", methods=["POST"])
def record():
  data = request.json
  label = data["label"]
  with _usb_lock:
    try:
      pos = run_async(get_liha_position())
      saved = load_json_file(POSITIONS_FILE)
      saved[label] = {"x": pos["x"], "y": pos["y"], "z": pos["z_all"]}
      save_json_file(POSITIONS_FILE, saved)
      return jsonify({"message": f"Recorded '{label}': X={pos['x']} Y={pos['y']} Z1={pos['z']}"})
    except Exception as e:
      return jsonify({"error": str(e)})


@app.route("/teach", methods=["POST"])
def teach():
  data = request.json
  field = data["field"]
  labware_name = data["labware"]
  with _usb_lock:
    try:
      pos = run_async(get_liha_position())
      z_val = pos["z"]
      edits = load_json_file(LABWARE_FILE)
      if labware_name not in edits:
        edits[labware_name] = {}
      edits[labware_name][field] = z_val
      save_json_file(LABWARE_FILE, edits)
      return jsonify({"message": f"{labware_name}.{field} = {z_val} ({z_val / 10:.1f}mm)"})
    except Exception as e:
      return jsonify({"error": str(e)})


@app.route("/action", methods=["POST"])
def action():
  data = request.json
  act = data["action"]
  with _usb_lock:
    try:
      result = run_async(do_action(act))
      liha_pos = run_async(get_liha_position())
      roma_pos = run_async(get_roma_position())
      return jsonify({"message": result, "liha": liha_pos, "roma": roma_pos})
    except Exception as e:
      return jsonify({"error": str(e)})


@app.route("/saved")
def saved():
  return jsonify(load_json_file(POSITIONS_FILE))


@app.route("/labware")
def labware_info():
  """Return all labware properties for the UI."""
  edits = load_json_file(LABWARE_FILE)
  result = {}

  def describe_resource(res, deck_ref, edits):
    """Build info dict for a single labware resource."""
    loc = res.get_location_wrt(deck_ref)
    info = {
      "type": type(res).__name__,
      "model": getattr(res, "model", ""),
      "size_x": round(res.get_size_x(), 1),
      "size_y": round(res.get_size_y(), 1),
      "size_z": round(res.get_size_z(), 1),
      "loc_x": round(loc.x, 1),
      "loc_y": round(loc.y, 1),
      "loc_z": round(loc.z, 1),
    }
    for attr in ("z_start", "z_dispense", "z_max", "area"):
      if hasattr(res, attr):
        val = getattr(res, attr)
        info[attr] = val
        if res.name in edits and attr in edits[res.name]:
          info[attr] = edits[res.name][attr]
          info[f"edited_{attr}"] = True
    if hasattr(res, "num_items"):
      info["num_items"] = res.num_items
    if hasattr(res, "num_items_x"):
      info["num_items_x"] = res.num_items_x
    if hasattr(res, "num_items_y"):
      info["num_items_y"] = res.num_items_y
    if hasattr(res, "item_dy"):
      info["item_dy"] = round(res.item_dy, 2)
    if hasattr(res, "get_tip"):
      try:
        tip = res.get_tip("A1")
        info["tip_length"] = tip.total_tip_length
        if hasattr(tip, "tip_type"):
          info["tip_type"] = str(tip.tip_type.value)
      except Exception:
        pass
    return info

  def find_labware(resource, deck_ref, edits):
    """Recursively find labware (TecanPlate, TecanTipRack) in resource tree."""
    items = {}
    for child in resource.children:
      ctype = type(child).__name__
      # If it's a plate or tip rack, add it
      if ctype in ("TecanPlate", "TecanTipRack"):
        items[child.name] = describe_resource(child, deck_ref, edits)
      # Recurse into children
      items.update(find_labware(child, deck_ref, edits))
    return items

  if evo is not None:
    deck_ref = evo.children[0] if evo.children else evo
    result = find_labware(deck_ref, deck_ref, edits)

  return jsonify(result)


# ============== Async helpers ==============


async def get_liha_position():
  resp_x = await driver.send_command("C5", command="RPX0")
  resp_y = await driver.send_command("C5", command="RPY0")
  resp_z = await driver.send_command("C5", command="RPZ0")
  x = resp_x["data"][0] if resp_x and resp_x.get("data") else 0
  y_data = resp_y["data"] if resp_y and resp_y.get("data") else [0]
  y = y_data[0] if isinstance(y_data, list) else y_data
  z_vals = resp_z["data"] if resp_z and resp_z.get("data") else [0] * 8
  return {"x": x, "y": y, "z": z_vals[0], "z_all": z_vals}


async def get_roma_position():
  try:
    resp_x = await driver.send_command("C1", command="RPX0")
    resp_y = await driver.send_command("C1", command="RPY0")
    resp_z = await driver.send_command("C1", command="RPZ0")
    resp_r = await driver.send_command("C1", command="RPR0")
    return {
      "x": resp_x["data"][0] if resp_x and resp_x.get("data") else 0,
      "y": resp_y["data"][0] if resp_y and resp_y.get("data") else 0,
      "z": resp_z["data"][0] if resp_z and resp_z.get("data") else 0,
      "r": resp_r["data"][0] if resp_r and resp_r.get("data") else 0,
    }
  except Exception:
    return {"x": 0, "y": 0, "z": 0, "r": 0}


async def do_jog(arm_name, axis, delta):
  if arm_name == "liha":
    module = "C5"
    if axis == "x":
      await driver.send_command(module, command=f"PRX{delta}")
    elif axis == "y":
      await driver.send_command(module, command=f"PRY{delta}")
    elif axis == "z":
      num_ch = evo.pip.num_channels
      z_params = ",".join([str(delta)] * num_ch)
      await driver.send_command(module, command=f"PRZ{z_params}")
  elif arm_name == "roma":
    module = "C1"
    if axis == "x":
      await driver.send_command(module, command=f"PRX{delta}")
    elif axis == "y":
      await driver.send_command(module, command=f"PRY{delta}")
    elif axis == "z":
      await driver.send_command(module, command=f"PRZ{delta}")
    elif axis == "r":
      await driver.send_command(module, command=f"PRR{delta}")


async def do_action(action):
  if action == "home":
    pip_be = evo.pip.backend
    z_range = pip_be._z_range
    num_ch = pip_be.num_channels
    await pip_be.liha.set_z_travel_height([z_range] * num_ch)
    await pip_be.liha.position_absolute_all_axis(45, 1031, 90, [z_range] * num_ch)
    return "LiHa homed"
  elif action == "park_roma":
    if evo.arm and evo.arm.backend.roma:
      await evo.arm.backend.park()
      return "RoMa parked"
    return "RoMa not available"
  elif action == "tips_status":
    resp = await driver.send_command("C5", command="RTS")
    status = resp["data"][0] if resp and resp.get("data") else "?"
    return f"Tip status: {status} (0=none, 255=all)"
  elif action == "ree":
    resp = await driver.send_command("C5", command="REE0")
    err = resp["data"][0] if resp and resp.get("data") else ""
    resp2 = await driver.send_command("C5", command="REE1")
    cfg = resp2["data"][0] if resp2 and resp2.get("data") else ""
    names = {0: "OK", 1: "Init failed", 7: "Not init", 25: "Tip not fetched"}
    lines = []
    for i, (ax, ec) in enumerate(zip(cfg, err)):
      code = ord(ec) - 0x40
      label = f"{ax}{i - 2}" if ax == "Z" else ax
      lines.append(f"{label}={names.get(code, f'err{code}')}")
    return " | ".join(lines)
  elif action == "lamp_green":
    await driver.send_command("O1", command="SSL1,1")
    return "Lamp: SSL1,1 (green on)"
  elif action == "lamp_off":
    await driver.send_command("O1", command="SSL1,0")
    return "Lamp: SSL1,0 (off)"
  elif action == "lamp_test":
    # Try various commands to find what controls the lamp
    results = []
    for cmd in ["SSL1,1", "SSL2,1", "SPS1", "SPS2", "SPS3"]:
      try:
        await driver.send_command("O1", command=cmd)
        results.append(f"O1,{cmd}: OK")
      except Exception as e:
        results.append(f"O1,{cmd}: {e}")
      import asyncio
      await asyncio.sleep(1)
    # Reset
    await driver.send_command("O1", command="SSL1,0")
    await driver.send_command("O1", command="SPS0")
    return " | ".join(results)
  elif action == "power_on":
    await driver.send_command("O1", command="SPN")
    await driver.send_command("O1", command="SPS3")
    return "Motor power: SPN + SPS3"
  elif action == "power_off":
    await driver.send_command("O1", command="SPS0")
    return "Motor power: SPS0 (off)"
  return f"Unknown action: {action}"


def load_json_file(path):
  if os.path.exists(path):
    with open(path, "r") as f:
      return json.load(f)
  return {}


def save_json_file(path, data):
  with open(path, "w") as f:
    json.dump(data, f, indent=2)


connected = False


def build_deck():
  """Build the deck and EVO device WITHOUT connecting to hardware."""
  global evo

  from labware_library import DiTi_50ul_SBS_LiHa_Air, Eppendorf_96_wellplate_250ul_Vb_skirted
  from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
  from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
  from pylabrobot.tecan.evo import TecanEVO

  deck = EVO150Deck()
  evo = TecanEVO(
    name="evo",
    deck=deck,
    diti_count=8,
    air_liha=True,
    has_roma=True,
    packet_read_timeout=30,
    read_timeout=120,
    write_timeout=120,
  )

  carrier = MP_3Pos("carrier")
  deck.assign_child_resource(carrier, rails=16)

  source_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("source")
  dest_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("dest")
  tip_rack = DiTi_50ul_SBS_LiHa_Air("tips")
  carrier[0] = source_plate
  carrier[1] = dest_plate
  carrier[2] = tip_rack

  print("Deck built (not connected).")


async def connect_evo():
  """Connect to the EVO hardware."""
  global driver, connected
  print("Connecting to EVO...")
  await evo.setup()
  driver = evo._driver
  connected = True
  print("EVO connected!")


async def disconnect_evo():
  """Disconnect from the EVO hardware."""
  global driver, connected
  print("Disconnecting...")
  await evo.stop()
  driver = None
  connected = False
  print("Disconnected.")


@app.route("/connect", methods=["POST"])
def connect():
  global connected
  if connected:
    return jsonify({"message": "Already connected", "connected": True})
  try:
    future = asyncio.run_coroutine_threadsafe(connect_evo(), loop)
    future.result(timeout=180)
    return jsonify({"message": "Connected!", "connected": True})
  except Exception as e:
    return jsonify({"error": str(e), "connected": False})


@app.route("/disconnect", methods=["POST"])
def disconnect():
  global connected
  if not connected:
    return jsonify({"message": "Not connected", "connected": False})
  try:
    future = asyncio.run_coroutine_threadsafe(disconnect_evo(), loop)
    future.result(timeout=30)
    return jsonify({"message": "Disconnected", "connected": False})
  except Exception as e:
    return jsonify({"error": str(e), "connected": connected})


@app.route("/connection_status")
def connection_status():
  return jsonify({"connected": connected})


def run_event_loop(lp):
  """Run the asyncio event loop in a background thread."""
  asyncio.set_event_loop(lp)
  lp.run_forever()


if __name__ == "__main__":
  # Install flask
  try:
    import flask  # noqa: F401
  except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "-q"])

  # Create event loop in background thread
  loop = asyncio.new_event_loop()
  thread = threading.Thread(target=run_event_loop, args=(loop,), daemon=True)
  thread.start()

  # Build deck (no hardware connection)
  build_deck()

  print("\n" + "=" * 50)
  print("  Open http://localhost:5050 in your browser")
  print("  Click 'Connect' to connect to the EVO")
  print("=" * 50 + "\n")

  app.run(host="0.0.0.0", port=5050, debug=False)
