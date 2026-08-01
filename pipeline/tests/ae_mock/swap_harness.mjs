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
function prop(keyed = false) {
  return { numKeys: keyed ? 3 : 0, expressionEnabled: false,
           _v: [100, 100], get value() { return this._v; },
           setValue(v) { this._v = v; } };
}
function mkLayer({ name, type = AVLayer, source = null, text = null,
                   keyedScale = false, enabled = true, start = 0, span = 190 }) {
  const l = Object.create(type.prototype);
  const scale = prop(keyedScale);
  const doc = { text };
  Object.assign(l, {
    name, source, enabled, nullLayer: false,
    startTime: start, inPoint: start, outPoint: start + span,
    _scale: scale, _doc: doc,
    property(id) {
      if (id === "ADBE Transform Group") return { property: () => scale };
      if (id === "ADBE Text Properties") return { property: () => ({
        get value() { return { text: doc.text }; },
        setValue(v) { doc.text = v.text; } }) };
      return null;
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
/* argv[4] = "long" 이면 인트로를 곡보다 길게 만든다. 길이로는 가려낼 수
   없는 프로젝트라, 이름을 기억해 두는 경로만 정답을 낼 수 있다. */
const introSpan = process.argv[4] === "long" ? 500 : 11;
const songComps = [];
const mainLayers = [];
let t = 0;
/* "long" 인트로는 곡 컴프를 복제해 만든 것이라 제목 텍스트까지 있다.
   길이로도 제목 유무로도 곡과 구분되지 않는, 실제로 겪은 구성이다. */
const introLayers = [mkLayer({ name: "intro.png", source: mkFootage({ name: "intro.png" }) })];
if (introSpan > 100) {
  introLayers.unshift(mkLayer({ name: "제목0", type: TextLayer, text: "Intro" }));
}
const intro = mkComp({ name: "Intro", layers: introLayers });
mainLayers.push(mkLayer({ name: "Intro", source: intro, start: t, span: introSpan }));
t += introSpan;
for (let i = 1; i <= 13; i++) {
  const comp = mkComp({ name: i === 1 ? "Change - Things" : `Change - Things ${i}`,
    layers: [ mkLayer({ name: `제목${i}`, type: TextLayer, text: `Song ${i}` }),
              mkLayer({ name: `old${i}.png`, source: mkFootage({ name: `old${i}.png` }),
                        keyedScale: true }) ] });
  songComps.push(comp);
  mainLayers.push(mkLayer({ name: comp.name, source: comp, start: t, span: 190 })); t += 190;
}
const stale = mkComp({ name: "Change - Things 20", layers: [
  mkLayer({ name: "제목20", type: TextLayer, text: "x" }),
  mkLayer({ name: "old20.png", source: mkFootage({ name: "old20.png" }) }) ] });
mainLayers.push(mkLayer({ name: stale.name, source: stale, start: t, span: 190, enabled: false }));
const main = mkComp({ name: "Main", layers: [
  mkLayer({ name: "LOGO.png", source: mkFootage({ name: "LOGO.png" }) }), ...mainLayers ] });
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

// ── 실행 ───────────────────────────────────────────────────
const src = fs.readFileSync(process.argv[2], "utf8");
(0, eval)(src);

// 이미지 폴더를 고른 것처럼: 일부러 뒤죽박죽 순서로 준다
const names = ["10-ten.png","2-two.png","1-one.png","13-thirteen.png","3-three.png",
               "4.png","5.png","6.png","7.png","8.png","9.png","11.png","12.png"];
pickedFolder = { fsName: "D:/imgs",
  getFiles: () => names.map((n) => Object.assign(new File(n), {})) };
reg.buttons["찾아보기…"].onClick();

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
process.stdout.write(JSON.stringify(out, null, 1));
