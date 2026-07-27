/*
 * After Effects ExtendScript 목업 하네스.
 *
 * AE 없이 assets/ae/*.jsx 를 실제로 실행해 보기 위한 최소 API 구현.
 * 스크립트를 돌린 뒤 결과 상태를 JSON 으로 stdout 에 내보내면
 * test_jsx.py 가 그걸 검사한다.
 *
 *   node harness.mjs build   <script.jsx>   # PLPIPE_JOB 주입 후 빌드 검사
 *   node harness.mjs dump    <script.jsx>   # 구조 리포트 생성 검사
 */

import fs from "node:fs";

// ── AE 클래스 (instanceof 판별용) ───────────────────────────
function AVLayer() {}
function TextLayer() {}
function ShapeLayer() {}
function CameraLayer() {}
function LightLayer() {}
function CompItem() {}
function FootageItem() {}
function SolidSource() {}
Object.assign(globalThis, { AVLayer, TextLayer, ShapeLayer, CameraLayer,
                            LightLayer, CompItem, FootageItem, SolidSource });

globalThis.ImportAsType = { FOOTAGE: "footage", COMP: "comp" };
globalThis.CloseOptions = { DO_NOT_SAVE_CHANGES: "nosave" };
globalThis.ImportOptions = function (file) {
  this.file = file;
  this.importAs = null;
  this.canImportAs = () => true;
};

// ── 상태 ───────────────────────────────────────────────────
const state = {
  items: [],
  renderQueue: [],
  savedTo: null,
  closed: false,
  report: null,
  imported: [],
  log: [],
};

function makeTransform(anim) {
  const props = {};
  for (const key of ["ADBE Anchor Point", "ADBE Position", "ADBE Scale",
                     "ADBE Rotate Z", "ADBE Opacity"]) {
    props[key] = {
      numKeys: 0,
      expressionEnabled: false,
      _value: [100, 100],
      get value() { return this._value; },
      setValue(v) { this._value = v; },
    };
  }
  for (const [key, kind] of Object.entries(anim || {})) {
    if (kind === "keys") props[key].numKeys = 3;
    else props[key].expressionEnabled = true;
  }
  return { property: (n) => props[n] || null, _props: props };
}

function makeLayer(spec) {
  const {
    name, type = AVLayer, source = null, text = null, anim = null,
    enabled = true, nullLayer = false, adjustmentLayer = false,
  } = spec;
  const layer = Object.create(type.prototype);
  const transform = makeTransform(anim);
  const doc = { text, _clone() { return { ...this }; } };
  Object.assign(layer, {
    name, source, enabled, nullLayer, adjustmentLayer,
    startTime: 0, inPoint: 0, outPoint: 0,
    _transform: transform,
    _textDoc: doc,
    property(id) {
      if (id === "ADBE Transform Group") return transform;
      if (id === "ADBE Text Properties") {
        return {
          property: () => ({
            get value() { return { text: doc.text }; },
            setValue(v) { doc.text = v.text; },
          }),
        };
      }
      return null;
    },
    replaceSource(newSource) { this.source = newSource; },
    moveBefore(other) {
      const list = this._owner.layers._list;
      list.splice(list.indexOf(this), 1);
      list.splice(list.indexOf(other), 0, this);
    },
    moveAfter(other) {
      const list = this._owner.layers._list;
      list.splice(list.indexOf(this), 1);
      list.splice(list.indexOf(other) + 1, 0, this);
    },
    moveToEnd() {
      const list = this._owner.layers._list;
      list.splice(list.indexOf(this), 1);
      list.push(this);
    },
    remove() {
      const owner = this._owner;
      if (owner) owner._removeLayer(this);
    },
  });
  return layer;
}

function makeLayerCollection(owner, layers) {
  const coll = {
    _list: layers.slice(),
    get length() { return this._list.length; },
    add(source) {
      const layer = makeLayer({ name: source.name, source });
      layer._owner = owner;
      layer.outPoint = source.duration || 0;
      // 실제 AE 는 새 레이어를 스택 맨 위(인덱스 1)에 넣는다.
      this._list.unshift(layer);
      return layer;
    },
  };
  // AE 의 LayerCollection 은 1-기반 인덱싱.
  for (let i = 0; i < 200; i++) {
    Object.defineProperty(coll, i + 1, {
      get() { return this._list[i]; },
      configurable: true,
    });
  }
  layers.forEach((l) => { l._owner = owner; });
  return coll;
}

function makeComp(spec) {
  const {
    name, width = 1920, height = 1080, frameRate = 24,
    duration = 180, layers = [],
  } = spec;
  const comp = Object.create(CompItem.prototype);
  Object.assign(comp, {
    name, width, height, frameRate, duration,
    _removeLayer(layer) {
      const i = comp.layers._list.indexOf(layer);
      if (i >= 0) comp.layers._list.splice(i, 1);
    },
    duplicate() {
      const copies = comp.layers._list.map((l) => makeLayer({
        name: l.name,
        type: Object.getPrototypeOf(l).constructor,
        source: l.source,
        text: l._textDoc.text,
        anim: Object.fromEntries(
          Object.entries(l._transform._props)
            .filter(([, p]) => p.numKeys > 0 || p.expressionEnabled)
            .map(([k, p]) => [k, p.numKeys > 0 ? "keys" : "expr"])
        ),
        enabled: l.enabled,
      }));
      const dup = makeComp({ name: name + " 2", width, height, frameRate,
                             duration, layers: copies });
      state.items.push(dup);
      return dup;
    },
    remove() {
      const i = state.items.indexOf(comp);
      if (i >= 0) state.items.splice(i, 1);
    },
  });
  comp.layers = makeLayerCollection(comp, layers);
  return comp;
}

function makeFootage(spec) {
  const {
    name, width = 2048, height = 1152, duration = 0,
    solid = false, hasAudio = false, hasVideo = true, path = null,
  } = spec;
  const f = Object.create(FootageItem.prototype);
  Object.assign(f, {
    name, width, height, duration, hasAudio, hasVideo, path,
    mainSource: solid ? Object.create(SolidSource.prototype) : {},
    remove() {},
  });
  return f;
}

// ── 시나리오: SLOT 프리컴프 + 13개를 품은 MAIN ───────────────
function buildScenario() {
  const placeholder = makeFootage({ name: "placeholder.png" });
  const scratch = makeFootage({ name: "scratch.wav", duration: 180,
                                hasAudio: true, hasVideo: false });

  const slot = makeComp({
    name: "SLOT",
    duration: 180,
    layers: [
      makeLayer({ name: "TITLE", type: TextLayer, text: "TRACK TITLE" }),
      makeLayer({ name: "INDEX", type: TextLayer, text: "00" }),
      // 켄번즈 줌: 스케일에 키프레임이 있으므로 plpipe 가 건드리면 안 된다.
      makeLayer({ name: "IMAGE", source: placeholder,
                  anim: { "ADBE Scale": "keys" } }),
      makeLayer({ name: "GRAIN", type: ShapeLayer }),
    ],
  });

  // MAIN 안의 슬롯이 아닌 레이어들. 재빌드 후에도 살아남아야 한다.
  const mainLayers = [
    makeLayer({ name: "AUDIO", source: scratch }),
    makeLayer({ name: "CHANNEL LOGO", source: makeFootage({ name: "logo.png" }) }),
  ];
  for (let i = 0; i < 13; i++) {
    mainLayers.push(makeLayer({ name: `SLOT ${i + 1}`, source: slot }));
  }
  mainLayers.push(makeLayer({
    name: "BACKGROUND",
    source: makeFootage({ name: "Navy Solid", solid: true }),
  }));
  const main = makeComp({ name: "MAIN", duration: 4717.5, layers: mainLayers });

  state.items = [main, slot, placeholder, scratch];
  return { main, slot, placeholder };
}

// AE 의 ItemCollection: .length = N, [1]..[N] 로 1-기반 접근.
function itemCollection() {
  const coll = {
    get length() { return state.items.length; },
    addComp(name, w, h, par, dur, fps) {
      const c = makeComp({ name, width: w, height: h,
                           duration: dur, frameRate: fps });
      state.items.push(c);
      return c;
    },
  };
  for (let i = 0; i < 500; i++) {
    Object.defineProperty(coll, i + 1, {
      get() { return state.items[i]; },
      configurable: true,
    });
  }
  return coll;
}


// ── app / project ──────────────────────────────────────────
function installApp() {
  const project = {
    get numItems() { return state.items.length; },
    file: { fsName: "/tpl/lofi.aep", parent: { fsName: "/tpl" } },
    item: (i) => state.items[i - 1],
    items: itemCollection(),
    importFile(opts) {
      const path = opts.file.fsName;
      state.imported.push(path);
      const f = makeFootage({ name: path.split("/").pop(), path });
      state.items.push(f);
      return f;
    },
    renderQueue: {
      get numItems() { return state.renderQueue.length; },
      item: (i) => state.renderQueue[i - 1],
      items: {
        add(comp) {
          const om = {
            templates: ["Lossless", "H.264", "AIFF 48kHz"],
            applied: null,
            file: null,
            applyTemplate(name) {
              if (!this.templates.includes(name)) throw new Error("no such OM: " + name);
              this.applied = name;
            },
          };
          const item = {
            comp,
            templates: ["Best Settings", "Draft Settings"],
            applied: null,
            applyTemplate(name) {
              if (!this.templates.includes(name)) throw new Error("no such RS: " + name);
              this.applied = name;
            },
            outputModule: () => om,
            _om: om,
            remove() {
              const i = state.renderQueue.indexOf(item);
              if (i >= 0) state.renderQueue.splice(i, 1);
            },
          };
          state.renderQueue.push(item);
          return item;
        },
      },
    },
    save(file) { state.savedTo = file.fsName; },
    close() { state.closed = true; },
  };

  globalThis.app = {
    version: "24.6 (mock)",
    project,
    open() {},
    beginUndoGroup(n) { state.log.push("undo:" + n); },
    endUndoGroup() {},
  };
}

globalThis.File = function (path) {
  this.fsName = path;
  this.exists = true;
  this.parent = { fsName: path.split("/").slice(0, -1).join("/"), create() {} };
  this.open = () => true;
  this.write = (s) => { state.report = s; };
  this.close = () => {};
};
globalThis.Folder = { desktop: { fsName: "/desktop" } };
globalThis.Window = function () {
  const node = { add: () => ({ ...node }), show() {}, close() {} };
  return node;
};
globalThis.$ = { writeln: (s) => state.log.push(String(s)) };

// ── 실행 ───────────────────────────────────────────────────
const [mode, scriptPath] = process.argv.slice(2);
const source = fs.readFileSync(scriptPath, "utf8");

installApp();
const scenario = buildScenario();

if (mode === "build" || mode === "build-full") {
  const slots = [];
  // 실제 파이프라인이 내는 것과 같은 모양: 프레임 정렬된 길이.
  const durations = [181.291666, 204.708333, 195.125];
  const gap = 1.5;
  for (let i = 0; i < durations.length; i++) {
    slots.push({
      index: i + 1,
      name: `PL_SLOT_${String(i + 1).padStart(2, "0")}`,
      title: `곡 "${i + 1}"`,
      label: String(i + 1).padStart(2, "0"),
      image: `/proj/drop/images/0${i + 1}.png`,
      duration: durations[i],
      output: `/proj/work/segments/0${i + 1}.mov`,
    });
  }

  // 3곡을 두 번 재생하는 배치 (full 모드에서만 쓰임).
  const placements = [];
  let cursor = 2.0;
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 0; i < durations.length; i++) {
      placements.push({
        slot: slots[i].name,
        start: cursor,
        end: cursor + durations[i] + gap,
      });
      cursor += durations[i] + gap;
    }
  }

  globalThis.PLPIPE_JOB = {
    mode: mode === "build-full" ? "full" : "segments",
    project: "/tpl/lofi.aep",
    save_as: "/proj/work/ae/demo.aep",
    names: {
      main_comp: "MAIN", slot_comp: "SLOT", image_layer: "IMAGE",
      title_layer: "TITLE", index_layer: "INDEX", audio_layer: "AUDIO",
    },
    video: { width: 1920, height: 1080, fps: 24 },
    slots,
    main: {
      name: "PL_MAIN",
      duration: cursor - gap + 4.0,
      audio: "/proj/out/master.wav",
      output: "/proj/work/ae/demo.mov",
      placements,
    },
    render: { settings: "Best Settings", module: "Lossless" },
  };
} else if (mode === "inspect") {
  globalThis.PLPIPE_JOB = {
    project: "/tpl/lofi.aep",
    save_as: "/proj/work/ae-template.json",
  };
}

(0, eval)(source);

// ── 결과 덤프 ──────────────────────────────────────────────
const built = state.items
  .filter((it) => it instanceof CompItem && /^PL_SLOT_/.test(it.name))
  .map((comp) => ({
    name: comp.name,
    duration: comp.duration,
    width: comp.width,
    height: comp.height,
    fps: comp.frameRate,
    layers: comp.layers._list.map((l) => ({
      name: l.name,
      source: l.source ? (l.source.path || l.source.name) : null,
      text: l._textDoc.text,
      outPoint: l.outPoint,
      scaleKeyed: l._transform._props["ADBE Scale"].numKeys > 0,
      scaleValue: l._transform._props["ADBE Scale"].value,
    })),
  }));

const mainComp = state.items.find(
  (it) => it instanceof CompItem && it.name === "PL_MAIN");

process.stdout.write(JSON.stringify({
  built,
  main: mainComp ? {
    name: mainComp.name,
    duration: mainComp.duration,
    layers: mainComp.layers._list.map((l) => ({
      name: l.name,
      source: l.source ? (l.source.path || l.source.name) : null,
      startTime: l.startTime,
      inPoint: l.inPoint,
      outPoint: l.outPoint,
    })),
  } : null,
  renderQueue: state.renderQueue.map((r) => ({
    comp: r.comp.name,
    settings: r.applied,
    module: r._om.applied,
    output: r._om.file ? r._om.file.fsName : null,
  })),
  imported: state.imported,
  savedTo: state.savedTo,
  closed: state.closed,
  report: state.report,
  originalSlotLayerCount: scenario.slot.layers.length,
  originalSlotImageSource: scenario.slot.layers._list
    .filter((l) => l.name === "IMAGE")
    .map((l) => l.source.name)[0],
}, null, 2));
