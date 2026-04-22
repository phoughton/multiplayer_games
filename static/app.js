(() => {
  "use strict";

  // ------------------------------------------------------------------ config
  const RECONNECT_WINDOW_MS = 5000;
  const RECONNECT_INTERVAL_MS = 500;
  const SPLASH_MS = 3000;

  // Server-side constants (kept in sync with server/game.py).
  const BALL_SIZE = 0.045;
  const PADDLE_WIDTH = 0.18;
  const PADDLE_THICKNESS = 0.025;
  const P1_PADDLE_Y = 0.75;
  const P2_PADDLE_Y = 0.25;
  // Your paddle sits at CONTROL_ZONE_TOP in your own view; the strip below
  // (bottom 25%) is the reserved "swipe here" zone. Opponent's mirror lives
  // in the top 25% automatically because of the slot-2 180-degree flip.
  const CONTROL_ZONE_TOP = 0.75;

  // --------------------------------------------------------------- DOM refs
  const views = {
    splash: document.getElementById("view-splash"),
    menu: document.getElementById("view-menu"),
    lobby: document.getElementById("view-lobby"),
    play: document.getElementById("view-play"),
    gameover: document.getElementById("view-gameover"),
  };
  const overlayReconnect = document.getElementById("overlay-reconnect");
  const reconnectCountdown = document.getElementById("reconnect-countdown");
  const toastEl = document.getElementById("toast");
  const canvas = document.getElementById("game-canvas");
  const ctx = canvas.getContext("2d");
  const playOverlay = document.getElementById("play-overlay");

  function show(name) {
    for (const [k, v] of Object.entries(views)) v.classList.toggle("active", k === name);
    if (name === "play") {
      // Canvas only gets a non-zero client size once its view is visible,
      // so size the backing buffer *after* the layout flips.
      requestAnimationFrame(resizeCanvas);
    }
  }
  function toast(msg, ms = 2500) {
    toastEl.textContent = msg;
    toastEl.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toastEl.classList.add("hidden"), ms);
  }

  // ------------------------------------------------------------------ audio
  let audioCtx = null;
  function ensureAudio() {
    if (!audioCtx && (window.AudioContext || window.webkitAudioContext)) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
  }
  function beep(freq, durMs = 80, type = "square", gain = 0.08) {
    if (!audioCtx) return;
    const t0 = audioCtx.currentTime;
    const osc = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    osc.connect(g);
    g.connect(audioCtx.destination);
    g.gain.setValueAtTime(gain, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + durMs / 1000);
    osc.start(t0);
    osc.stop(t0 + durMs / 1000);
  }
  function playSfx(kind) {
    if (!audioCtx) return;
    if (kind === "paddle") beep(520, 70, "square");
    else if (kind === "wall") beep(260, 60, "square");
    else if (kind === "goal") { beep(330, 140, "square"); setTimeout(() => beep(165, 160, "square"), 140); }
    else if (kind === "game_over") {
      const notes = [523, 392, 330, 196];
      notes.forEach((f, i) => setTimeout(() => beep(f, 220, "square"), i * 180));
    }
  }

  // ------------------------------------------------------------------ state
  const App = {
    ws: null,
    code: null,
    slot: null,          // 1 or 2
    token: null,
    state: null,         // latest server snapshot
    opponentHere: false,
    lastSentPaddleX: null,
    currentFingerX: null,
    reconnectDeadline: 0,
    reconnectTimer: null,
    menuPending: null,   // "create" or {join: code} after open
    rematchWaiting: false,
  };

  // -------------------------------------------------------------- WebSocket
  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/ws`;
  }

  function openWs(onOpen) {
    const ws = new WebSocket(wsUrl());
    App.ws = ws;
    ws.onopen = () => onOpen && onOpen();
    ws.onmessage = (evt) => {
      let msg;
      try { msg = JSON.parse(evt.data); } catch { return; }
      handleMessage(msg);
    };
    ws.onclose = () => handleWsClose();
    ws.onerror = () => {};
    return ws;
  }

  function send(msg) {
    if (App.ws && App.ws.readyState === WebSocket.OPEN) {
      App.ws.send(JSON.stringify(msg));
    }
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "room_created":
        App.code = msg.code;
        App.slot = msg.slot;
        App.token = msg.token;
        document.getElementById("lobby-code").textContent = msg.code;
        show("lobby");
        break;
      case "joined":
        App.code = msg.code;
        App.slot = msg.slot;
        App.token = msg.token;
        App.opponentHere = true;
        show("play");
        break;
      case "opponent_joined":
        App.opponentHere = true;
        if (activeView() === "lobby") show("play");
        break;
      case "opponent_disconnected":
        toast("OPPONENT DISCONNECTED...");
        break;
      case "opponent_left":
        App.opponentHere = false;
        const reason = msg.reason || "left";
        toast(`OPPONENT ${reason.toUpperCase()}`);
        goMenu();
        break;
      case "state":
        App.state = msg.state;
        if (App.state.sfx) playSfx(App.state.sfx);
        if (activeView() !== "play" && App.opponentHere && App.state.status !== "game_over") show("play");
        if (App.state.status === "game_over") showGameOver();
        break;
      case "rematch_requested":
        if (msg.slot !== App.slot) {
          document.getElementById("rematch-status").textContent = "OPPONENT WANTS A REMATCH";
        }
        break;
      case "rematch_started":
        App.rematchWaiting = false;
        document.getElementById("rematch-status").textContent = "";
        show("play");
        break;
      case "ejected":
        toast("YOU WERE EJECTED");
        goMenu();
        break;
      case "reconnected":
        dismissReconnect();
        if (App.opponentHere) show("play");
        break;
      case "error":
        if (msg.reason === "no_such_room" || msg.reason === "full") {
          const err = document.getElementById("join-error");
          err.textContent = msg.reason === "full" ? "ROOM IS FULL" : "NO SUCH ROOM";
          err.classList.remove("hidden");
        } else if (msg.reason === "bad_reconnect") {
          dismissReconnect();
          goMenu();
          toast("SESSION LOST");
        }
        break;
    }
  }

  function handleWsClose() {
    // If we never got into a room, just go back to menu silently.
    if (!App.code || !App.token) {
      if (activeView() !== "menu" && activeView() !== "splash") goMenu();
      return;
    }
    beginReconnect();
  }

  // ------------------------------------------------------------ reconnect
  function beginReconnect() {
    App.reconnectDeadline = Date.now() + RECONNECT_WINDOW_MS;
    overlayReconnect.classList.remove("hidden");
    tickReconnect();
    App.reconnectTimer = setInterval(tickReconnect, RECONNECT_INTERVAL_MS);
  }

  function tickReconnect() {
    const remaining = Math.max(0, App.reconnectDeadline - Date.now());
    reconnectCountdown.textContent = `${(remaining / 1000).toFixed(1)}S`;
    if (remaining <= 0) {
      dismissReconnect();
      toast("CONNECTION LOST");
      goMenu();
      return;
    }
    if (App.ws && App.ws.readyState === WebSocket.OPEN) return;
    if (App.ws && App.ws.readyState === WebSocket.CONNECTING) return;
    openWs(() => {
      send({ type: "reconnect", code: App.code, token: App.token });
    });
  }

  function dismissReconnect() {
    overlayReconnect.classList.add("hidden");
    if (App.reconnectTimer) { clearInterval(App.reconnectTimer); App.reconnectTimer = null; }
  }

  // ---------------------------------------------------------------- menu
  function goMenu() {
    App.code = null;
    App.slot = null;
    App.token = null;
    App.opponentHere = false;
    App.state = null;
    App.rematchWaiting = false;
    document.getElementById("join-panel").classList.add("hidden");
    document.getElementById("code-input").value = "";
    document.getElementById("join-error").classList.add("hidden");
    document.getElementById("rematch-status").textContent = "";
    if (App.ws) {
      try { App.ws.close(); } catch {}
      App.ws = null;
    }
    show("menu");
  }

  function activeView() {
    for (const [k, v] of Object.entries(views)) if (v.classList.contains("active")) return k;
    return null;
  }

  // -------------------------------------------------------------- render
  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", resizeCanvas);
  window.addEventListener("orientationchange", resizeCanvas);

  function viewCoords(x, y) {
    // Flip 180° for player 2 so their paddle is at the bottom of their screen.
    if (App.slot === 2) return [1 - x, 1 - y];
    return [x, y];
  }

  function render() {
    requestAnimationFrame(render);
    if (activeView() !== "play" || !App.state) return;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);

    // Centre dashed line (horizontal, since play is vertical).
    ctx.strokeStyle = "#fff";
    ctx.setLineDash([8, 10]);
    ctx.beginPath();
    ctx.moveTo(0, H / 2);
    ctx.lineTo(W, H / 2);
    ctx.stroke();
    ctx.setLineDash([]);

    // Control-zone divider + swipe hint (bottom 25% of *your* screen).
    drawControlZone(W, H);

    // Paddles
    drawPaddle(App.state.p1_x, P1_PADDLE_Y, W, H);
    drawPaddle(App.state.p2_x, P2_PADDLE_Y, W, H);

    // Ball (retro square)
    const [bx, by] = viewCoords(App.state.ball.x, App.state.ball.y);
    const bs = BALL_SIZE * Math.min(W, H);
    ctx.fillStyle = "#fff";
    ctx.fillRect(bx * W - bs / 2, by * H - bs / 2, bs, bs);

    // Scores
    drawScores(W, H);

    // Status overlay (COUNTDOWN, PAUSED)
    if (App.state.status === "countdown") {
      const n = Math.ceil(App.state.countdown_remaining);
      playOverlay.textContent = n > 0 ? String(n) : "GO";
      playOverlay.classList.remove("hidden");
    } else if (App.state.status === "paused") {
      playOverlay.textContent = "PAUSED";
      playOverlay.classList.remove("hidden");
    } else {
      playOverlay.classList.add("hidden");
    }
  }

  function drawPaddle(canonicalX, canonicalY, W, H) {
    const [vx, vy] = viewCoords(canonicalX, canonicalY);
    const pw = PADDLE_WIDTH * W;
    const pt = PADDLE_THICKNESS * H;
    ctx.fillStyle = "#fff";
    ctx.fillRect(vx * W - pw / 2, vy * H - pt / 2, pw, pt);
  }

  function drawControlZone(W, H) {
    // Your control strip is the bottom 25% of the screen (below your paddle).
    // With the slot-2 180-degree flip this ends up as the bottom of the
    // viewport regardless of slot, so we draw in view-space directly.
    const zoneTop = CONTROL_ZONE_TOP * H;

    // Dashed boundary line between play area and control zone.
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,0.45)";
    ctx.setLineDash([6, 8]);
    ctx.beginPath();
    ctx.moveTo(0, zoneTop);
    ctx.lineTo(W, zoneTop);
    ctx.stroke();
    ctx.restore();

    // Swipe hint text, centered in the zone.
    const mid = (zoneTop + H) / 2;
    const primarySize = Math.round(Math.min(W, H) * 0.055);
    const secondarySize = Math.round(Math.min(W, H) * 0.03);
    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `bold ${primarySize}px "Courier New", monospace`;
    ctx.fillText("← SWIPE HERE →", W / 2, mid - secondarySize * 0.6);
    ctx.font = `${secondarySize}px "Courier New", monospace`;
    ctx.fillText("MOVE PADDLE", W / 2, mid + primarySize * 0.6);
    ctx.restore();
  }

  function drawScores(W, H) {
    const you = App.slot === 1 ? App.state.score_p1 : App.state.score_p2;
    const them = App.slot === 1 ? App.state.score_p2 : App.state.score_p1;
    ctx.fillStyle = "#fff";
    ctx.font = `bold ${Math.round(Math.min(W, H) * 0.08)}px monospace`;
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(String(them), 16, 12);
    ctx.textBaseline = "bottom";
    ctx.fillText(String(you), 16, H - 12);
  }

  // -------------------------------------------------------------- input
  function setFingerFromEvent(clientX) {
    const rect = canvas.getBoundingClientRect();
    const x = (clientX - rect.left) / rect.width;
    App.currentFingerX = Math.max(0, Math.min(1, x));
  }

  canvas.addEventListener("touchstart", (e) => {
    e.preventDefault();
    ensureAudio();
    if (e.touches.length) setFingerFromEvent(e.touches[0].clientX);
  }, { passive: false });
  canvas.addEventListener("touchmove", (e) => {
    e.preventDefault();
    if (e.touches.length) setFingerFromEvent(e.touches[0].clientX);
  }, { passive: false });
  canvas.addEventListener("touchend", (e) => {
    e.preventDefault();
  }, { passive: false });

  let dragging = false;
  canvas.addEventListener("mousedown", (e) => { dragging = true; ensureAudio(); setFingerFromEvent(e.clientX); });
  window.addEventListener("mouseup", () => { dragging = false; });
  window.addEventListener("mousemove", (e) => { if (dragging) setFingerFromEvent(e.clientX); });

  function sendInputLoop() {
    if (activeView() === "play" && App.currentFingerX !== null) {
      const x = App.currentFingerX;
      if (App.lastSentPaddleX === null || Math.abs(x - App.lastSentPaddleX) > 0.002) {
        send({ type: "input", paddle_x: x });
        App.lastSentPaddleX = x;
      }
    }
    requestAnimationFrame(sendInputLoop);
  }

  // ------------------------------------------------------------ game over
  function showGameOver() {
    const iWon = App.state.winner === App.slot;
    document.getElementById("gameover-winner").textContent = iWon ? "YOU WIN" : "YOU LOSE";
    show("gameover");
  }

  // ------------------------------------------------------------ UI events
  document.getElementById("btn-new").addEventListener("click", () => {
    ensureAudio();
    openWs(() => send({ type: "create" }));
  });
  document.getElementById("btn-join").addEventListener("click", () => {
    ensureAudio();
    document.getElementById("join-panel").classList.remove("hidden");
    document.getElementById("code-input").focus();
  });
  document.getElementById("btn-join-cancel").addEventListener("click", () => {
    document.getElementById("join-panel").classList.add("hidden");
    document.getElementById("join-error").classList.add("hidden");
  });
  document.getElementById("btn-join-submit").addEventListener("click", () => {
    const code = document.getElementById("code-input").value.trim().toUpperCase();
    if (code.length !== 6) {
      const err = document.getElementById("join-error");
      err.textContent = "CODE MUST BE 6 CHARS";
      err.classList.remove("hidden");
      return;
    }
    openWs(() => send({ type: "join", code }));
  });
  document.getElementById("code-input").addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
  });

  document.getElementById("btn-lobby-cancel").addEventListener("click", goMenu);
  document.getElementById("btn-eject").addEventListener("click", () => {
    if (confirm("Eject your opponent?")) send({ type: "eject" });
  });
  document.getElementById("btn-rematch").addEventListener("click", () => {
    App.rematchWaiting = true;
    document.getElementById("rematch-status").textContent = "WAITING FOR OPPONENT...";
    send({ type: "rematch" });
  });
  document.getElementById("btn-quit").addEventListener("click", () => {
    send({ type: "leave" });
    goMenu();
  });

  // ---------------------------------------------------------------- boot
  show("splash");
  setTimeout(() => {
    if (activeView() === "splash") show("menu");
  }, SPLASH_MS);
  resizeCanvas();
  requestAnimationFrame(render);
  requestAnimationFrame(sendInputLoop);
})();
