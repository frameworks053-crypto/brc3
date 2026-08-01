/*
 * swap_images.jsx 를 AE 없이 돌려보기 위한 하네스.
 * ScriptUI 까지 흉내내어, 실제로 이미지가 어느 컴프에 매칭되는지 확인한다.
 */
import fs from "node:fs";

// ── AE 타입 ────────────────────────────────────────────────
function AVLayer() {} function TextLayer() {} function ShapeLayer() {}
function CompItem() {} function FootageItem() {} function SolidSource() {}
Object.assign(globalThis, { AVLayer, TextLayer, ShapeLayer, CompItem,
                            FootageItem, SolidSource });
globalThis.ImportAsType = { FOOTAGE: "footage" };
globalThis.ImportOptions = function (f) { this.file = f; this.canImportAs = () => true; };

const applied = [];
function prop(value, keyed = false) {
  return { numKeys: keyed ? 3 : 0, expressionEnabled: false,
           _v: value, get value() { return this._v; },
           setValue(v) { this._v = v; } };
}

/* 글자 폭 모형. AE 는 실제 폰트로 재지만, 여기서는 "글자 수 × 크기" 로
   근사한다. 스크립트가 재고-줄이고-다시 재는 흐름을 검증하는 게 목적이라
   비례하기만 하면 된다. */
const CHAR_W = 0.55, LINE_H = 1.2;

function mkLayer({ name, type = AVLayer, source = null, text = null,
                   keyedScale = false, enabled = true, start = 0, span = 190,
                   fontSize = 180, comment = "",
                   pos = [1920, 1080], anchor = [1920, 1080],
                   rectW = null, rectH = null }) {
  const l = Object.create(type.prototype);
  const scale = prop([100, 100], keyedScale);
  const position = prop(pos), anchorProp = prop(anchor), rotation = prop(0);
  const doc = { text, fontSize };
  Object.assign(l, {
    name, source, enabled, nullLayer: false, parent: null, comment,
    startTime: start, inPoint: start, outPoint: start + span,
    _scale: scale, _doc: doc,
    property(id) {
      if (id === "ADBE Transform Group") return { property(pid) {
        if (pid === "ADBE Position") return position;
        if (pid === "ADBE Anchor Point") return anchorProp;
        if (pid === "ADBE Rotate Z") return rotation;
        return scale;                       // 기본은 스케일 (기존 호출부)
      } };
      if (id === "ADBE Text Properties") return { property: () => ({
        get value() { return { text: doc.text, fontSize: doc.fontSize }; },
        setValue(v) { doc.text = v.text; doc.fontSize = v.fontSize; } }) };
      return null;
    },
    /* 텍스트는 가운데 정렬이라 상자가 앵커를 중심으로 좌우로 자란다. */
    sourceRectAtTime() {
      if (type === TextLayer) {
        const w = String(doc.text || "").length * doc.fontSize * CHAR_W;
        const h = doc.fontSize * LINE_H;
        return { left: -w / 2, top: -h, width: w, height: h };
      }
      const w = rectW !== null ? rectW : (source ? source.width : 0);
      const h = rectH !== null ? rectH : (source ? source.height : 0);
      return { left: 0, top: 0, width: w, height: h };
    },
    replaceSource(src) { this.source = src; applied.push([name, src.name]); },
  });
  return l;
}
function mkComp({ name, layers = [], width = 3840, height = 2160 }) {
  const c = Object.create(CompItem.prototype);
  const coll = { get length() { return layers.length; } };
  layers.forEach((l, i) => { coll[i + 1] = l; });
  Object.assign(c, { name, width, height, frameRate: 29.97, duration: 3600, layers: coll });
  return c;
}
function mkFootage({ name, width = 2944, height = 1648, still = true }) {
  const f = Object.create(FootageItem.prototype);
  Object.assign(f, { name, width, height, duration: still ? 0 : 6,
                     hasVideo: true, hasAudio: false, mainSource: {} });
  return f;
}

// ── 시나리오: 인트로 1 + 곡 13 + 꺼진 잔재 1 ────────────────
/* argv[3] = 지난번에 기억해 둔 제외 목록, 그 뒤는 전부 옵션 플래그.
   "long"      인트로를 곡보다 길게 (길이로는 가려낼 수 없는 구성)
   "preshrunk" 지난 회차에 줄여 둔 제목으로 시작
   "shuffle"   이미지 섞기 버튼을 눌러 본다
   "reorder"   섞은 뒤 "원래 순서" 로 되돌린다
   "nointro"   인트로에 1번 이미지 넣기를 끈다 */
const flags = new Set(process.argv.slice(4));
const introSpan = flags.has("long") ? 500 : 11;
const songComps = [];
const mainLayers = [];
let t = 0;
/* "long" 인트로는 곡 컴프를 복제해 만든 것이라 제목 텍스트까지 있다.
   길이로도 제목 유무로도 곡과 구분되지 않는, 실제로 겪은 구성이다. */
const title = (name, text, extra = {}) => mkLayer(Object.assign(
  { name, type: TextLayer, text, pos: [1920, 1900], anchor: [0, 0] }, extra));
const introLayers = [mkLayer({ name: "intro.png", source: mkFootage({ name: "intro.png" }) })];
if (introSpan > 100) {
  introLayers.unshift(title("제목0", "Intro"));
}
const intro = mkComp({ name: "Intro", layers: introLayers });
mainLayers.push(mkLayer({ name: "Intro", source: intro, start: t, span: introSpan }));
t += introSpan;
/* "preshrunk" 면 지난 회차에 줄여 둔 상태로 시작한다. 코멘트에 원래 크기가
   적혀 있으므로, 짧은 제목이 오면 그 크기로 돌아가야 한다. */
const preshrunk = flags.has("preshrunk");
for (let i = 1; i <= 13; i++) {
  const comp = mkComp({ name: i === 1 ? "Change - Things" : `Change - Things ${i}`,
    layers: [ title(`제목${i}`, `Song ${i}`, preshrunk
                    ? { fontSize: 40, comment: "plpipe-base-size:180" } : {}),
              mkLayer({ name: `old${i}.png`, source: mkFootage({ name: `old${i}.png` }),
                        keyedScale: true }) ] });
  songComps.push(comp);
  mainLayers.push(mkLayer({ name: comp.name, source: comp, start: t, span: 190 })); t += 190;
}
const stale = mkComp({ name: "Change - Things 20", layers: [
  title("제목20", "x"),
  mkLayer({ name: "old20.png", source: mkFootage({ name: "old20.png" }) }) ] });
mainLayers.push(mkLayer({ name: stale.name, source: stale, start: t, span: 190, enabled: false }));
/* 로고는 화면 왼쪽 위에 놓인 작은 이미지. 제목이 여기까지 닿으면 안 된다. */
const main = mkComp({ name: "Main", layers: [
  mkLayer({ name: "LOGO.png", source: mkFootage({ name: "LOGO.png", width: 300, height: 300 }),
            pos: [200, 200], anchor: [150, 150] }),
  ...mainLayers ] });
const items = [main, intro, ...songComps, stale];

const store = {};
if (process.argv[3]) store["plpipe/notSongs"] = process.argv[3];
globalThis.app = {
  project: { numItems: items.length, item: (i) => items[i - 1],
             importFile: (o) => mkFootage({ name: o.file.name }) },
  beginUndoGroup() {}, endUndoGroup() {},
  settings: {
    haveSetting: (sec, key) => (sec + "/" + key) in store,
    getSetting: (sec, key) => store[sec + "/" + key] || "",
    saveSetting: (sec, key, val) => { store[sec + "/" + key] = val; },
  },
};
globalThis.__store = store;

// ── UI 목업 ────────────────────────────────────────────────
const reg = { buttons: {}, listbox: null, checkbox: {}, statictext: [] };
function control(type, text) {
  const c = {
    type, text: text ?? "", subItems: [], items: [], _sel: null,
    preferredSize: {}, alignChildren: [], alignment: [], margins: 0,
    characters: 0, enabled: true, value: false, selection: null,
    add(kind, ...rest) {
      // ScriptUI: 보통 add(type, bounds, text) 지만 ListBox.add 는 add(type, text).
      const content = kind === "item" ? rest[0] : rest[1];
      if (kind === "item") {
        const it = control("item", content);
        it.index = this.items.length;
        for (let i = 0; i < 5; i++) it.subItems.push(control("sub", ""));
        this.items.push(it);
        return it;
      }
      const child = control(kind, content);
      if (kind === "button") reg.buttons[content] = child;
      if (kind === "checkbox") reg.checkbox[content] = child;
      if (kind === "listbox") reg.listbox = child;
      if (kind === "statictext") reg.statictext.push(child);
      if (kind === "dropdownlist") {
        child.items = (content || []).map((t, i) => ({ text: t, index: i }));
        child.selection = child.items[0];
      }
      return child;
    },
    removeAll() { this.items = []; },
    show() {}, close() {}, center() {},
  };
  return c;
}
globalThis.Window = function () { return control("window"); };

let pickedFolder = null;
globalThis.File = function (p) { this.name = p; this.displayName = p; };
globalThis.Folder = { selectDialog: () => pickedFolder };
globalThis.alert = (m) => { globalThis.__alert = m; };
globalThis.confirm = () => true;
globalThis.decodeURI = (s) => s;

// 섞기 결과를 매번 같게 하려고 난수를 고정한다.
let seed = 12345;
Math.random = () => {
  seed = (seed * 1103515245 + 12345) % 2147483648;
  return seed / 2147483648;
};

// ── 실행 ───────────────────────────────────────────────────
const src = fs.readFileSync(process.argv[2], "utf8");
(0, eval)(src);

// 이미지 폴더를 고른 것처럼: 일부러 뒤죽박죽 순서로 준다
const names = ["10-ten.png","2-two.png","1-one.png","13-thirteen.png","3-three.png",
               "4.png","5.png","6.png","7.png","8.png","9.png","11.png","12.png"];
pickedFolder = { fsName: "D:/imgs",
  getFiles: () => names.map((n) => Object.assign(new File(n), {})) };
reg.buttons["찾아보기…"].onClick();

function setCheck(label, on) {
  const cb = reg.checkbox[label];
  cb.value = on;
  if (cb.onClick) cb.onClick();
}
if (flags.has("nointro")) setCheck("인트로에도 1번 이미지", false);
if (flags.has("shuffle")) reg.buttons["이미지 섞기"].onClick();
if (flags.has("reorder")) {
  reg.buttons["이미지 섞기"].onClick();
  reg.buttons["원래 순서"].onClick();
}

const lb = reg.listbox;
const out = {
  rows: lb.items.map((it) => ({
    n: it.text, comp: it.subItems[0].text, span: it.subItems[1].text,
    image: it.subItems[2].text, why: it.subItems[3].text,
  })),
  status: reg.statictext[reg.statictext.length - 2].text,
};

// 적용까지 눌러 본다. 실제 교체 결과와, 다음 회차용으로 저장된 제외 목록.
reg.buttons["적용"].onClick();
out.applied = applied.map((pair) => ({ layer: pair[0], image: pair[1] }));
out.saved = globalThis.__store["plpipe/notSongs"];
out.alert = globalThis.__alert;
// 제목 레이어의 최종 상태 — 자동 줄이기 결과를 확인한다.
out.titles = [];
[intro, ...songComps, stale].forEach((comp) => {
  for (let i = 1; i <= comp.layers.length; i++) {
    const layer = comp.layers[i];
    if (!(layer instanceof TextLayer)) continue;
    out.titles.push({ comp: comp.name, text: layer._doc.text,
                      fontSize: layer._doc.fontSize, comment: layer.comment });
  }
});
process.stdout.write(JSON.stringify(out, null, 1));
