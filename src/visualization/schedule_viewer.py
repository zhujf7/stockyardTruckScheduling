from __future__ import annotations

import json
import re
import time
import webbrowser
from collections import defaultdict
from pathlib import Path
from typing import Any

from paths import (
    DEFAULT_SCHEDULE_PATH,
    INSTANCES_DIR,
    LOCAL_MAP_PATH,
    OUTPUT_DIR,
)
_ROLLING_KEY = re.compile(r"^(?P<vehicle>.+)_iter(?P<iteration>\d+)$")
_ENTRY_PATH_IDS = (5372,)
_VEHICLE_GEOMETRY = {
    "width": 2.6,
    "forwardFront": 9.2,
    "forwardRear": 2.4,
}


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"JSON file does not exist: {path}")
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _parse_node(node: str) -> list[int]:
    values = re.findall(r"-?\d+", node)
    if len(values) != 2:
        raise ValueError(f"Invalid rolling schedule node: {node!r}")
    return [int(values[0]), int(values[1])]


def _rolling_schedules(schedule_data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Convert rolling-window plans into the portions that actually execute."""

    raw_schedule = schedule_data.get("schedule")
    run_config = schedule_data.get("run_config", {})
    period = float(run_config.get("rolling_period", 0))
    if not isinstance(raw_schedule, dict) or not raw_schedule or period <= 0:
        raise ValueError(
            "Rolling result must contain 'schedule' and a positive "
            "run_config.rolling_period."
        )

    occurrences: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for key, tasks in raw_schedule.items():
        match = _ROLLING_KEY.match(str(key))
        if match is None or not isinstance(tasks, dict):
            continue
        occurrences[match.group("vehicle")].append(
            (int(match.group("iteration")), tasks)
        )

    schedules: dict[str, list[dict[str, Any]]] = {}
    for vehicle_id, plans in occurrences.items():
        plans.sort(key=lambda item: item[0])
        executed: list[dict[str, Any]] = []
        for plan_index, (iteration, tasks) in enumerate(plans):
            window_start = (iteration - 1) * period
            window_end = iteration * period if plan_index + 1 < len(plans) else float("inf")
            ordered_tasks = sorted(tasks.items(), key=lambda item: float(item[1][0]))
            if (
                ordered_tasks
                and float(ordered_tasks[0][1][0]) < window_start - 1e-7
            ):
                raise ValueError(
                    f"Rolling plan {vehicle_id}_iter{iteration} starts before "
                    f"its reoptimization time {window_start:.6f}. Rerun the "
                    "rolling experiment with the current scheduler."
                )
            for node_text, interval in ordered_tasks:
                start = max(float(interval[0]), window_start)
                end = min(float(interval[1]), window_end)
                if end <= start + 1e-9:
                    continue
                task = {
                    "node": _parse_node(node_text),
                    "start_time": start,
                    "end_time": end,
                }
                if (
                    executed
                    and executed[-1]["node"] == task["node"]
                    and task["start_time"] <= executed[-1]["end_time"] + 1e-7
                ):
                    executed[-1]["end_time"] = max(
                        executed[-1]["end_time"], task["end_time"]
                    )
                else:
                    executed.append(task)
        if executed:
            schedules[vehicle_id] = executed
    return schedules


def _extract_schedules(schedule_data: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(schedule_data, dict):
        raise ValueError("Schedule JSON must contain an object.")
    schedules = schedule_data.get("vehicle_schedules")
    if isinstance(schedules, dict) and schedules:
        return schedules
    return _rolling_schedules(schedule_data)


def _vehicle_initial_states(
    schedule_data: dict[str, Any],
    schedule_path: Path,
) -> dict[str, dict[str, Any]]:
    """Load release times and initial nodes from the associated instance."""

    run_config = schedule_data.get("run_config", {})
    instance_name = (
        run_config.get("instance")
        if isinstance(run_config, dict)
        else None
    )
    if not instance_name:
        instance_name = schedule_path.stem

    for category in ("static", "rolling"):
        instance_path = INSTANCES_DIR / category / f"{instance_name}.json"
        if not instance_path.is_file():
            continue
        instance_data = _load_json(instance_path)
        if not isinstance(instance_data, dict):
            break
        initial_states: dict[str, dict[str, Any]] = {}
        for vehicle in instance_data.values():
            if not isinstance(vehicle, dict):
                continue
            vehicle_id = vehicle.get("vehicle_id")
            release_time = vehicle.get("release_time")
            initial_node = vehicle.get("current_node", vehicle.get("gate_node"))
            if (
                vehicle_id is None
                or release_time is None
                or not isinstance(initial_node, list)
                or len(initial_node) != 2
            ):
                continue
            initial_path = int(initial_node[0])
            initial_states[str(vehicle_id)] = {
                "releaseTime": float(release_time),
                "initialNode": [initial_path, int(initial_node[1])],
                "inStockyard": initial_path not in _ENTRY_PATH_IDS,
            }
        return initial_states

    raise FileNotFoundError(
        f"Instance JSON for {instance_name!r} was not found under {INSTANCES_DIR}."
    )


def _viewer_payload(map_path: Path, schedule_path: Path) -> dict[str, Any]:
    map_data = _load_json(map_path)
    schedule_data = _load_json(schedule_path)

    if not isinstance(map_data, list):
        raise ValueError("Map JSON must be an array of path records.")

    schedules = _extract_schedules(schedule_data)
    vehicle_initial_states = _vehicle_initial_states(schedule_data, schedule_path)
    if not isinstance(schedules, dict) or not schedules:
        raise ValueError(
            "Schedule JSON must contain vehicle schedules or a rolling result."
        )

    paths: dict[str, list[list[float]]] = {}
    for record in map_data:
        if not isinstance(record, dict) or "pathId" not in record:
            continue
        points = record.get("points")
        if not isinstance(points, list) or not points:
            continue
        paths[str(record["pathId"])] = points

    if not paths:
        raise ValueError("Map JSON does not contain any usable paths.")

    tasks = [
        task
        for vehicle_tasks in schedules.values()
        if isinstance(vehicle_tasks, list)
        for task in vehicle_tasks
        if isinstance(task, dict)
        and "start_time" in task
        and "end_time" in task
        and "node" in task
    ]
    if not tasks:
        raise ValueError("The vehicle schedules do not contain any usable tasks.")

    return {
        "paths": paths,
        "schedules": schedules,
        "vehicleInitialStates": vehicle_initial_states,
        "vehicleGeometry": _VEHICLE_GEOMETRY,
        "timeMin": min(0.0, min(float(task["start_time"]) for task in tasks)),
        "timeMax": max(float(task["end_time"]) for task in tasks),
        "source": str(schedule_path.resolve()),
    }


def generate_schedule_viewer(
    schedule_path: str | Path = DEFAULT_SCHEDULE_PATH,
    output_path: str | Path | None = None,
    *,
    map_path: str | Path = LOCAL_MAP_PATH,
    open_browser: bool = False,
) -> Path:
    """Generate a self-contained browser viewer for one vehicle schedule."""

    schedule = Path(schedule_path).resolve()
    output = (
        Path(output_path).resolve()
        if output_path is not None
        else _default_output_path(schedule)
    )
    local_map = Path(map_path).resolve()
    payload = json.dumps(
        _viewer_payload(local_map, schedule),
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c")

    html = _HTML_TEMPLATE.replace("__VIEWER_DATA__", payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")

    if open_browser:
        # Cache-bust regenerated local files so an already open browser tab
        # cannot keep running an older embedded script.
        webbrowser.open(f"{output.as_uri()}?v={time.time_ns()}")
    return output


def _default_output_path(schedule_path: Path) -> Path:
    """Build a descriptive output name from a result's relative path."""

    try:
        relative = schedule_path.resolve().relative_to(
            (INSTANCES_DIR.parent.parent / "results").resolve()
        )
        name_parts = [*relative.parts[:-1], relative.stem]
    except ValueError:
        name_parts = [schedule_path.stem]

    safe_parts = [
        re.sub(r"[^A-Za-z0-9_-]+", "_", part).strip("_")
        for part in name_parts
    ]
    filename = "_".join(part for part in safe_parts if part)
    return (OUTPUT_DIR / f"{filename or 'schedule'}.html").resolve()


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>车辆动态调度展示</title>
  <style>
    :root { color-scheme: light; font-family: Inter, "Microsoft YaHei", sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #f3f5f7; color: #17202a; }
    .app { height: 100vh; display: grid; grid-template-rows: auto 1fr; }
    .toolbar {
      display: grid; grid-template-columns: auto minmax(180px, 1fr) auto auto auto;
      gap: 12px; align-items: center; padding: 12px 16px;
      background: white; border-bottom: 1px solid #dfe4e8;
      box-shadow: 0 2px 8px rgba(21, 31, 41, .06); z-index: 2;
    }
    button, select {
      border: 1px solid #cbd3da; border-radius: 7px; background: white;
      min-height: 34px; padding: 0 12px; color: inherit; cursor: pointer;
    }
    button:hover { background: #f0f5f8; }
    input[type="range"] { width: 100%; accent-color: #1677ff; }
    .time { min-width: 145px; font-variant-numeric: tabular-nums; text-align: right; }
    .status { color: #566573; font-size: 13px; white-space: nowrap; }
    .stage { position: relative; min-height: 0; padding: 14px; }
    svg {
      width: 100%; height: 100%; display: block; background: #fbfcfd;
      border: 1px solid #dfe4e8; border-radius: 10px;
      box-shadow: 0 8px 30px rgba(21, 31, 41, .08);
    }
    .road { fill: none; stroke: #c7cdd3; stroke-width: 1.2; vector-effect: non-scaling-stroke; }
    .vehicle-truck { pointer-events: none; }
    .truck-shadow { fill: rgba(23, 32, 42, .22); }
    .truck-chassis { fill: #26323d; stroke: white; stroke-width: .18; }
    .truck-bed { fill: var(--truck-color); stroke: rgba(23, 32, 42, .55); stroke-width: .13; }
    .truck-bed-inner { fill: rgba(255, 255, 255, .18); stroke: rgba(23, 32, 42, .35); stroke-width: .1; }
    .truck-cab { fill: var(--truck-color); stroke: rgba(23, 32, 42, .65); stroke-width: .14; }
    .truck-windshield { fill: #bfe4f2; stroke: #385467; stroke-width: .1; }
    .truck-window { fill: #91c9dc; stroke: #385467; stroke-width: .08; }
    .truck-wheel { fill: #151a1f; }
    .truck-hub { fill: #aab5bd; }
    .truck-light { fill: #ffe082; stroke: #8a6b00; stroke-width: .06; }
    .truck-divider { stroke: rgba(23, 32, 42, .38); stroke-width: .1; }
    .vehicle-truck.is-working .truck-bed {
      stroke: #f59e0b; stroke-width: .28;
    }
    .vehicle-truck.is-working .truck-bed-inner { fill: rgba(245, 158, 11, .2); }
    .vehicle-label {
      fill: #17202a; paint-order: stroke; stroke: white; stroke-width: 3px;
      stroke-linejoin: round; font-weight: 600; pointer-events: none;
    }
    .hint {
      position: absolute; right: 26px; bottom: 24px; padding: 7px 10px;
      border-radius: 7px; background: rgba(255,255,255,.88); color: #67737e;
      font-size: 12px; pointer-events: none;
    }
    @media (max-width: 760px) {
      .toolbar { grid-template-columns: auto 1fr auto; }
      .status, .speed { display: none; }
    }
  </style>
</head>
<body>
<div class="app">
  <div class="toolbar">
    <button id="play" type="button">播放</button>
    <input id="timeline" type="range">
    <div id="time" class="time"></div>
    <label class="speed">速度 <select id="speed">
      <option value="1">1x</option><option value="5">5x</option>
      <option value="10" selected>10x</option><option value="30">30x</option>
    </select></label>
    <div id="status" class="status"></div>
  </div>
  <div class="stage">
    <svg id="map" role="img" aria-label="车辆调度动态地图">
      <g id="roads"></g><g id="vehicles"></g><g id="labels"></g>
    </svg>
    <div class="hint">←/→ 调整时间　空格 播放/暂停</div>
  </div>
</div>
<script id="viewer-data" type="application/json">__VIEWER_DATA__</script>
<script>
(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("viewer-data").textContent);
  const svg = document.getElementById("map");
  const roadLayer = document.getElementById("roads");
  const vehicleLayer = document.getElementById("vehicles");
  const labelLayer = document.getElementById("labels");
  const slider = document.getElementById("timeline");
  const playButton = document.getElementById("play");
  const speedSelect = document.getElementById("speed");
  const timeLabel = document.getElementById("time");
  const statusLabel = document.getElementById("status");
  const ns = "http://www.w3.org/2000/svg";

  const allPoints = Object.values(data.paths).flat();
  const xs = allPoints.map(point => Number(point[0]));
  const ys = allPoints.map(point => Number(point[1]));
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const width = Math.max(maxX - minX, 1), height = Math.max(maxY - minY, 1);
  const padding = Math.max(width, height) * 0.025;
  const screenY = y => minY + maxY - Number(y);
  svg.setAttribute("viewBox", `${minX-padding} ${minY-padding} ${width+2*padding} ${height+2*padding}`);

  const roadFragment = document.createDocumentFragment();
  for (const points of Object.values(data.paths)) {
    const polyline = document.createElementNS(ns, "polyline");
    polyline.setAttribute("class", "road");
    polyline.setAttribute("points", points.map(p => `${p[0]},${screenY(p[1])}`).join(" "));
    roadFragment.appendChild(polyline);
  }
  roadLayer.appendChild(roadFragment);

  const scale = Math.max(width, height);
  const fontSize = Math.max(scale * 0.009, 3.8);
  const marks = new Map();
  const scheduleEntries = Object.entries(data.schedules).map(([vehicleId, tasks]) => [
    vehicleId,
    tasks
      .filter(task => task && Array.isArray(task.node))
      .sort((left, right) => Number(left.start_time) - Number(right.start_time)),
  ]);
  const vehicleInitialStates = data.vehicleInitialStates || {};
  const colorFor = id => {
    let hash = 0;
    for (const char of id) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
    return `hsl(${Math.abs(hash) % 360} 72% 48%)`;
  };

  function svgElement(name, className, attributes = {}) {
    const element = document.createElementNS(ns, name);
    if (className) element.setAttribute("class", className);
    for (const [attribute, value] of Object.entries(attributes)) {
      element.setAttribute(attribute, value);
    }
    return element;
  }

  function createTruck(vehicleId) {
    const truck = svgElement("g", "vehicle-truck");
    truck.style.setProperty("--truck-color", colorFor(vehicleId));
    truck.style.display = "none";

    // Local +x is the physical front of the truck.  The complete silhouette
    // stays inside [-2.4, 9.2] x [-1.3, 1.3] metres.
    truck.appendChild(svgElement("rect", "truck-shadow", {
      x: -2.22, y: -1.16, width: 11.45, height: 2.42, rx: .38,
      transform: "translate(.12 .12)",
    }));
    truck.appendChild(svgElement("rect", "truck-chassis", {
      x: -2.4, y: -1.3, width: 11.6, height: 2.6, rx: .38,
    }));

    // Six wheels make the top-down silhouette read as a heavy truck.
    for (const axleX of [-1.48, 4.72, 7.32]) {
      for (const wheelY of [-1.29, .94]) {
        truck.appendChild(svgElement("rect", "truck-wheel", {
          x: axleX - .42, y: wheelY, width: .84, height: .35, rx: .12,
        }));
        truck.appendChild(svgElement("circle", "truck-hub", {
          cx: axleX, cy: wheelY + .175, r: .105,
        }));
      }
    }

    // Cargo body and its recessed upper panel.
    truck.appendChild(svgElement("rect", "truck-bed", {
      x: -2.18, y: -1.08, width: 7.12, height: 2.16, rx: .2,
    }));
    truck.appendChild(svgElement("path", "truck-bed-inner", {
      d: "M-1.82,-.78 L4.56,-.78 L4.56,.78 L-1.82,.78 Z",
    }));
    for (const dividerX of [-.7, .72, 2.14, 3.56]) {
      truck.appendChild(svgElement("line", "truck-divider", {
        x1: dividerX, y1: -.77, x2: dividerX, y2: .77,
      }));
    }

    // Cab, windscreen, side windows, bonnet and headlights.
    truck.appendChild(svgElement("path", "truck-cab", {
      d: "M5.08,-1.08 L7.72,-1.08 Q8.86,-.96 9.02,-.58 L9.02,.58 Q8.86,.96 7.72,1.08 L5.08,1.08 Z",
    }));
    truck.appendChild(svgElement("path", "truck-windshield", {
      d: "M7.03,-.86 L8.08,-.72 L8.08,.72 L7.03,.86 Z",
    }));
    truck.appendChild(svgElement("path", "truck-window", {
      d: "M5.48,-.86 L6.78,-.86 L6.78,-.2 L5.48,-.28 Z",
    }));
    truck.appendChild(svgElement("path", "truck-window", {
      d: "M5.48,.86 L6.78,.86 L6.78,.2 L5.48,.28 Z",
    }));
    truck.appendChild(svgElement("line", "truck-divider", {
      x1: 8.28, y1: -.62, x2: 8.28, y2: .62,
    }));
    for (const lightY of [-.72, .72]) {
      truck.appendChild(svgElement("rect", "truck-light", {
        x: 8.82, y: lightY - .16, width: .26, height: .32, rx: .07,
      }));
    }
    return truck;
  }

  for (const vehicleId of Object.keys(data.schedules).sort()) {
    const truck = createTruck(vehicleId);
    vehicleLayer.appendChild(truck);

    const label = document.createElementNS(ns, "text");
    label.setAttribute("class", "vehicle-label");
    label.setAttribute("font-size", fontSize);
    label.setAttribute("text-anchor", "middle");
    label.textContent = vehicleId;
    label.style.display = "none";
    labelLayer.appendChild(label);
    marks.set(vehicleId, {truck, label});
  }

  slider.min = data.timeMin;
  slider.max = data.timeMax;
  slider.step = 0.1;
  slider.value = data.timeMin;
  const keyStep = Math.max(0.1, (data.timeMax - data.timeMin) / 10000);
  let currentTime = Number(data.timeMin);
  let playing = false;
  let playbackTimer = null;
  let previousTick = null;

  function poseAt(task, time) {
    const start = Number(task.start_time), end = Number(task.end_time);
    // vehicleStateAt decides whether a task occupies the current instant.
    // poseAt must still accept the exact endpoint so a vehicle can remain
    // parked there during unloading/work gaps.
    if (time < start || time > end || !Array.isArray(task.node)) return null;
    const points = data.paths[String(task.node[0])];
    if (!points || points.length === 0) return null;
    if (points.length === 1) {
      return {position: [Number(points[0][0]), Number(points[0][1])], direction: [1, 0]};
    }
    const scheduledDuration = Math.max(0, end - start);
    const hasProgressMetadata = Number.isFinite(Number(task.path_progress_start));
    const taskTravel = Number(task.travel_time);
    const travelDuration = Number.isFinite(taskTravel)
      ? taskTravel
      : scheduledDuration;
    const progressStart = hasProgressMetadata
      ? Math.max(0, Math.min(1, Number(task.path_progress_start)))
      : 0;
    const progress = travelDuration > 0
      ? progressStart + (1-progressStart) * Math.max(
          0,
          Math.min(1, (time - start) / travelDuration),
        )
      : 1;
    const forwardIndex = progress * (points.length - 1);
    const reversePath = Number(task.node[1]) === 3 || Number(task.node[1]) === 4;
    const index = reversePath ? points.length - 1 - forwardIndex : forwardIndex;
    const left = Math.floor(index), right = Math.min(left + 1, points.length - 1);
    const ratio = index - left;
    const position = [
      Number(points[left][0]) * (1-ratio) + Number(points[right][0]) * ratio,
      Number(points[left][1]) * (1-ratio) + Number(points[right][1]) * ratio,
    ];

    let segmentStart;
    let segmentEnd;
    if (reversePath) {
      // At the exact reverse-path endpoint index is zero.  Use the final
      // travelled segment (points[1] -> points[0]) so the stopped pose is
      // identical to the pose immediately before arrival.
      segmentEnd = Math.max(0, Math.min(points.length - 2, Math.floor(index)));
      segmentStart = segmentEnd + 1;
    } else {
      segmentStart = Math.min(points.length - 2, Math.floor(index));
      segmentEnd = segmentStart + 1;
    }
    let dx = Number(points[segmentEnd][0]) - Number(points[segmentStart][0]);
    let dy = Number(points[segmentEnd][1]) - Number(points[segmentStart][1]);
    const norm = Math.hypot(dx, dy);
    if (norm > 1e-9) {
      dx /= norm;
      dy /= norm;
    } else {
      dx = 1;
      dy = 0;
    }
    return {position, direction: [dx, dy]};
  }

  function vehicleDisplay(pose, action) {
    const [x, y] = pose.position;
    const [fx, fy] = pose.direction;
    const reversing = Number(action) === 2 || Number(action) === 4;
    const hx = reversing ? -fx : fx;
    const hy = reversing ? -fy : fy;
    const angle = Math.atan2(-hy, hx) * 180 / Math.PI;
    const lx = -hy, ly = hx;
    const halfWidth = Number(data.vehicleGeometry.width) / 2;
    return {
      transform: `translate(${x} ${screenY(y)}) rotate(${angle})`,
      labelPosition: [x + lx*(halfWidth + 2.2), y + ly*(halfWidth + 2.2)],
    };
  }

  function vehicleStateAt(vehicleId, tasks, time) {
    if (tasks.length === 0) return null;
    let low = 0, high = tasks.length - 1, candidate = -1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (Number(tasks[middle].start_time) <= time) {
        candidate = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    if (candidate < 0) {
      const firstTask = tasks[0];
      const initialState = vehicleInitialStates[vehicleId];
      if (!initialState || !initialState.inStockyard) return null;
      const initialNode = initialState.initialNode;
      if (!Array.isArray(initialNode)) return null;
      return {
        task: {
          ...firstTask,
          node: initialNode,
          start_time: Number(data.timeMin),
          end_time: Math.max(Number(data.timeMin) + 1, Number(firstTask.start_time)),
        },
        poseTime: Number(data.timeMin),
        mode: "parked",
      };
    }
    const task = tasks[candidate];
    if (time < Number(task.end_time)) {
      const taskTravel = Number(task.travel_time);
      const scheduledDuration = Math.max(
        0,
        Number(task.end_time) - Number(task.start_time),
      );
      const travelDuration = Number.isFinite(taskTravel)
        ? taskTravel
        : scheduledDuration;
      const movementEnd = Number(task.start_time) + travelDuration;
      return {
        task,
        poseTime: Math.min(time, movementEnd - Number.EPSILON),
        mode: time < movementEnd ? "moving" : "waiting",
      };
    }

    // A positive gap between two route actions represents stationary work
    // (for example unloading). Keep the truck at the end pose of the previous
    // action until the next action starts.
    const nextTask = tasks[candidate + 1];
    if (nextTask && time < Number(nextTask.start_time)) {
      return {
        task,
        poseTime: Number(task.end_time),
        mode: "working",
      };
    }
    return null;
  }

  function render(time) {
    currentTime = Math.max(
      Number(data.timeMin),
      Math.min(Number(data.timeMax), Number(time)),
    );
    let active = 0;
    let moving = 0;
    let working = 0;
    let waiting = 0;
    let parked = 0;
    for (const [vehicleId, tasks] of scheduleEntries) {
      const state = vehicleStateAt(vehicleId, tasks, currentTime);
      const task = state ? state.task : null;
      const pose = state ? poseAt(task, state.poseTime) : null;
      const {truck, label} = marks.get(vehicleId);
      if (!pose) {
        truck.style.display = "none";
        truck.classList.remove("is-working");
        label.style.display = "none";
        continue;
      }
      active += 1;
      if (state.mode === "working") working += 1;
      else if (state.mode === "waiting") waiting += 1;
      else if (state.mode === "parked") parked += 1;
      else moving += 1;
      const display = vehicleDisplay(pose, task.node[1]);
      truck.setAttribute("transform", display.transform);
      truck.classList.toggle("is-working", state.mode === "working");
      label.setAttribute("x", display.labelPosition[0]);
      label.setAttribute("y", screenY(display.labelPosition[1]));
      truck.style.display = "";
      label.style.display = "";
    }
    slider.value = currentTime;
    timeLabel.textContent = `${currentTime.toFixed(1)} s / ${Number(data.timeMax).toFixed(1)} s`;
    statusLabel.textContent = `${playing ? "播放中" : "已暂停"} · 行驶 ${moving} · 初始停放 ${parked} · 路内等待 ${waiting} · 作业 ${working} · 场内 ${active} / ${scheduleEntries.length}`;
  }

  function setPlaying(value) {
    if (playbackTimer !== null) {
      clearInterval(playbackTimer);
      playbackTimer = null;
    }
    if (value && currentTime >= Number(data.timeMax)) {
      currentTime = Number(data.timeMin);
    }
    playing = value;
    playButton.textContent = playing ? "暂停" : "播放";
    previousTick = performance.now();
    render(currentTime);
    if (playing) playbackTimer = setInterval(tick, 33);
  }

  function tick() {
    if (!playing) return;
    const timestamp = performance.now();
    const elapsed = Math.max(0, (timestamp - previousTick) / 1000);
    previousTick = timestamp;
    const next = currentTime + elapsed * Number(speedSelect.value);
    if (next >= data.timeMax) {
      render(Number(data.timeMax)); setPlaying(false); return;
    }
    render(next);
  }

  slider.addEventListener("input", () => render(Number(slider.value)));
  playButton.addEventListener("click", () => setPlaying(!playing));
  document.addEventListener("keydown", event => {
    if (event.code === "Space") { event.preventDefault(); setPlaying(!playing); return; }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    const next = Math.max(data.timeMin, Math.min(data.timeMax, currentTime + direction * keyStep));
    render(next);
  });

  const query = new URLSearchParams(window.location.search);
  const requestedTime = Number(query.get("time"));
  render(Number.isFinite(requestedTime) && query.has("time") ? requestedTime : Number(data.timeMin));
  if (query.get("autoplay") === "1") {
    setPlaying(true);
  }
})();
</script>
</body>
</html>
"""
