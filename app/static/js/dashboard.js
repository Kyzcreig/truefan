(() => {
  "use strict";

  const POLL_MS = 5000;
  const TOKEN_KEY = "truefan.uiWriteToken";
  const byId = (id) => document.getElementById(id);
  const elements = {
    safetyState: byId("safety-state"),
    safetyReason: byId("safety-reason"),
    stateOrb: byId("state-orb"),
    duty: byId("duty-percent"),
    mode: byId("bmc-mode"),
    agent: byId("agent-state"),
    refresh: byId("last-refresh"),
    maxDrive: byId("temp-max-drive"),
    cpu: byId("temp-cpu"),
    board: byId("temp-board"),
    nvme: byId("temp-nvme"),
    driveGrid: byId("drive-grid"),
    driveCount: byId("drive-count"),
    fanGrid: byId("fan-grid"),
    fanCount: byId("fan-count"),
    backend: byId("backend-name"),
    lock: byId("control-lock"),
    token: byId("write-token"),
    saveToken: byId("save-token"),
    slider: byId("duty-slider"),
    sliderValue: byId("slider-value"),
    ttl: byId("ttl-seconds"),
    apply: byId("apply-duty"),
    result: byId("control-result"),
    lockHint: byId("lock-hint"),
    profiles: Array.from(document.querySelectorAll("[data-profile]")),
  };

  let serverAllowsControl = false;
  let actionInFlight = false;

  function text(element, value) {
    if (element) element.textContent = String(value);
  }

  function titleCase(value) {
    const words = String(value || "unknown").replaceAll("-", " ").replaceAll("_", " ");
    return words.replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function finiteNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function temperature(element, value, warm, hot) {
    if (!element) return;
    element.classList.remove("temp-warm", "temp-hot");
    const numeric = finiteNumber(value);
    if (numeric === null) {
      text(element, "--°");
      return;
    }
    text(element, `${numeric.toFixed(1)}°`);
    if (numeric > hot) element.classList.add("temp-hot");
    else if (numeric >= warm) element.classList.add("temp-warm");
  }

  function telemetryRow(name, value, temperatureValue = null) {
    const row = document.createElement("div");
    row.className = "telemetry-row";
    const label = document.createElement("span");
    label.textContent = name;
    label.title = name;
    const reading = document.createElement("strong");
    reading.textContent = value;
    if (temperatureValue !== null) {
      if (temperatureValue > 44) reading.classList.add("temp-hot");
      else if (temperatureValue >= 41) reading.classList.add("temp-warm");
    }
    row.append(label, reading);
    return row;
  }

  function empty(container, message) {
    container.replaceChildren();
    const item = document.createElement("p");
    item.className = "empty-state";
    item.textContent = message;
    container.appendChild(item);
  }

  const LOCK_EXPLANATIONS = {
    hot_threshold: "Hot threshold: a drive is above 44°C or CPU above 70°C. Fans are forced to 100% and only the Emergency profile is accepted until drives cool to ≤40°C and CPU to ≤60°C.",
    cooling_band: "Cooling: recovering from a hot incident. Duty is held at ≥50% until drives reach ≤40°C and CPU ≤60°C.",
    sensor_failure_fail_closed: "Sensor failure: temperature readings are unavailable, so lowering fan duty is refused (fail-closed).",
    "status unavailable": "The dashboard cannot reach the control agent. Monitoring and control are unavailable.",
    "Agent offline": "The control agent is offline. Fan control is unavailable; monitoring may be stale.",
    "Applying override": "A control request is in flight — waiting for the write and read-back verification.",
  };

  function lockExplanation(reason) {
    return LOCK_EXPLANATIONS[reason]
      || (reason ? `Controls locked: ${titleCase(reason)}.` : "Controls are locked by the safety policy.");
  }

  function updateControlAvailability(enabled, reason, safetyState) {
    serverAllowsControl = Boolean(enabled);
    const disabled = !serverAllowsControl || actionInFlight;
    const explanation = disabled ? lockExplanation(reason) : "";
    // During a hot lock the policy still accepts 100% — keep Emergency usable.
    const hotLocked = disabled && safetyState === "hot" && !actionInFlight;
    const controls = [elements.slider, elements.ttl, elements.apply, ...elements.profiles];
    controls.forEach((control) => {
      const isEmergency = control.dataset && control.dataset.profile === "emergency";
      const controlDisabled = disabled && !(hotLocked && isEmergency);
      control.disabled = controlDisabled;
      if (controlDisabled) control.title = explanation;
      else if (hotLocked && isEmergency) control.title = "Hot threshold active — Emergency (100%) is the only accepted override.";
      else control.removeAttribute("title");
    });
    elements.lock.dataset.locked = String(!serverAllowsControl);
    if (serverAllowsControl) {
      text(elements.lock, "Safety checks active");
      elements.lock.removeAttribute("title");
    } else {
      text(elements.lock, reason ? `Locked: ${titleCase(reason)}` : "Controls locked");
      elements.lock.title = explanation;
    }
    if (elements.lockHint) {
      text(elements.lockHint, explanation);
      elements.lockHint.hidden = !disabled;
    }
  }

  function render(payload) {
    const data = payload && typeof payload === "object" ? payload : {};
    const backend = data.backend && typeof data.backend === "object" ? data.backend : {};
    const safety = data.safety && typeof data.safety === "object" ? data.safety : {};
    const temperatures = backend.temperatures && typeof backend.temperatures === "object"
      ? backend.temperatures
      : {};
    const state = safety.state || "unknown";

    text(elements.safetyState, titleCase(state));
    text(elements.safetyReason, titleCase(safety.reason || "Awaiting safety evaluation"));
    elements.stateOrb.dataset.state = state;
    const duty = finiteNumber(backend.duty_percent);
    text(elements.duty, duty === null ? "--%" : `${duty}%`);
    text(elements.mode, titleCase(backend.mode));
    text(elements.agent, data.agent_available ? "Online" : "Degraded");
    text(elements.refresh, `Updated ${new Date().toLocaleTimeString()}`);
    text(elements.backend, `Backend: ${backend.backend || "unknown"}`);

    temperature(elements.maxDrive, temperatures.max_drive_c, 41, 44);
    temperature(elements.cpu, temperatures.cpu_c, 61, 70);
    temperature(elements.board, temperatures.board_c, 55, 70);
    temperature(elements.nvme, temperatures.nvme_c, 60, 75);

    const drives = Array.isArray(data.drives) ? data.drives : [];
    elements.driveGrid.replaceChildren();
    drives.forEach((drive) => {
      const value = Number(drive.temperature_c);
      elements.driveGrid.appendChild(
        telemetryRow(drive.name || "unknown", Number.isFinite(value) ? `${value.toFixed(1)}°C` : "--", value)
      );
    });
    if (!drives.length) empty(elements.driveGrid, "No drive temperatures available.");
    text(elements.driveCount, `${drives.length} drive${drives.length === 1 ? "" : "s"}`);

    const fans = backend.fan_rpms && typeof backend.fan_rpms === "object" ? backend.fan_rpms : {};
    const fanEntries = Object.entries(fans);
    elements.fanGrid.replaceChildren();
    fanEntries.forEach(([name, rpm]) => elements.fanGrid.appendChild(telemetryRow(name, `${rpm} RPM`)));
    if (!fanEntries.length) empty(elements.fanGrid, "No fan RPM readings available.");
    text(elements.fanCount, `${fanEntries.length} fan${fanEntries.length === 1 ? "" : "s"}`);

    const locked = safety.controls_locked === true;
    updateControlAvailability(data.pwm_control_enabled === true && !locked, locked ? (safety.reason || "") : "Agent offline", state);
  }

  function showResult(message, kind = "") {
    elements.result.className = `control-result${kind ? ` ${kind}` : ""}`;
    text(elements.result, message);
  }

  function token() {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  }

  async function post(path, body) {
    const writeToken = token();
    if (!writeToken) throw new Error("Enter the UI write token for this tab first.");
    const response = await fetch(path, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${writeToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok !== true) {
      throw new Error(payload?.error?.message || `Control request failed (HTTP ${response.status})`);
    }
    return payload.data || {};
  }

  async function runAction(action) {
    if (actionInFlight) return;
    actionInFlight = true;
    updateControlAvailability(serverAllowsControl, "Applying override", "");
    showResult("Applying and verifying the requested duty…");
    try {
      const result = await action();
      const verified = result?.readback?.verified === true ? "verified" : "not verified";
      showResult(
        `Requested ${result.requested_duty}% · effective ${result.effective_duty}% · read-back ${verified}`,
        "success"
      );
      await poll();
    } catch (error) {
      showResult(error.message || "Control request failed.", "error");
    } finally {
      actionInFlight = false;
      updateControlAvailability(serverAllowsControl, "Controls locked", "");
    }
  }

  async function poll() {
    try {
      const response = await fetch("/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (_error) {
      render({
        agent_available: false,
        pwm_control_enabled: false,
        backend: {},
        safety: { state: "sensor-failure", reason: "status unavailable", controls_locked: true },
        drives: [],
      });
    }
  }

  elements.token.value = token();
  elements.saveToken.addEventListener("click", () => {
    const value = elements.token.value.trim();
    if (value) {
      sessionStorage.setItem(TOKEN_KEY, value);
      showResult("UI write token is active for this browser tab.", "success");
    } else {
      sessionStorage.removeItem(TOKEN_KEY);
      showResult("UI write token cleared from this browser tab.");
    }
  });
  elements.slider.addEventListener("input", () => text(elements.sliderValue, `${elements.slider.value}%`));
  elements.apply.addEventListener("click", () => runAction(() => post("/api/control", {
    duty_percent: Number(elements.slider.value),
    ttl_seconds: Number(elements.ttl.value),
  })));
  elements.profiles.forEach((button) => {
    button.addEventListener("click", () => runAction(() => post(`/api/profile/${button.dataset.profile}`, {
      ttl_seconds: Number(elements.ttl.value),
    })));
  });

  poll();
  window.setInterval(poll, POLL_MS);
})();
