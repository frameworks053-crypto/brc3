/*
 * 이미지 넣기 — After Effects 단독 실행 스크립트
 *
 * 설치도 설정도 명령 프롬프트도 필요 없습니다.
 * AE 에서 프로젝트를 열어둔 뒤
 *   File > Scripts > Run Script File...
 * 로 이 파일을 실행하세요.
 *
 * 하는 일:
 *   · 메인 컴프에 놓인 곡 컴프들을 타임라인 순서대로 찾는다
 *   · 고른 폴더의 이미지를 파일명 번호 순서대로 하나씩 넣는다
 *   · 인트로에는 첫 곡과 같은 이미지를 넣는다
 *   · 제목이 길어 로고에 닿으면 글자 크기를 줄인다
 *   · (선택) 곡 제목 텍스트도 파일명으로 바꾼다
 *
 * "이미지 섞기" 를 누르면 순서가 무작위로 다시 배분됩니다. 표에서 결과를
 * 보고 마음에 들 때까지 눌러도 되고, "원래 순서" 로 되돌릴 수도 있습니다.
 *
 * 적용 전에 무엇이 어디로 들어가는지 표로 보여주고, 적용 후에는
 * Ctrl+Z 한 번으로 전부 되돌릴 수 있습니다.
 *
 * ExtendScript 는 ES3 수준이라 let/const/forEach/JSON 을 쓸 수 없습니다.
 */

(function () {
    var IMAGE_EXT = /\.(png|jpg|jpeg|tif|tiff|psd|webp)$/i;

    // ── 기본 헬퍼 ───────────────────────────────────────────
    function layerKind(layer) {
        if (layer instanceof TextLayer) return "text";
        if (layer instanceof ShapeLayer) return "shape";
        if (layer.nullLayer) return "null";
        if (layer.source instanceof CompItem) return "precomp";
        if (layer.source instanceof FootageItem) {
            var src = layer.source;
            if (src.mainSource instanceof SolidSource) return "solid";
            if (src.hasAudio && !src.hasVideo) return "audio";
            if (src.hasVideo && src.duration === 0) return "still";
            return "footage";
        }
        return "other";
    }

    function pad2(n) {
        return (n < 10 ? "0" : "") + n;
    }

    function baseName(file) {
        var name = file.displayName || file.name;
        return decodeURI(name).replace(IMAGE_EXT, "");
    }

    /* 파일명 앞 번호로 정렬한다. "10-foo" 가 "2-bar" 보다 뒤에 오도록
       숫자로 비교한다. 번호가 없으면 이름순으로 뒤에 붙인다. */
    function leadingNumber(name) {
        var m = /^\s*(\d{1,3})(?!\d)/.exec(name);
        return m ? parseInt(m[1], 10) : null;
    }

    function sortImages(files) {
        files.sort(function (a, b) {
            var na = leadingNumber(baseName(a));
            var nb = leadingNumber(baseName(b));
            if (na !== null && nb !== null) return na - nb;
            if (na !== null) return -1;
            if (nb !== null) return 1;
            var sa = baseName(a).toLowerCase();
            var sb = baseName(b).toLowerCase();
            return sa < sb ? -1 : (sa > sb ? 1 : 0);
        });
        return files;
    }

    /* 순서를 무작위로 섞는다 (Fisher-Yates). 원본은 건드리지 않는다. */
    function shuffled(items) {
        var out = items.slice();
        for (var i = out.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1));
            var tmp = out[i]; out[i] = out[j]; out[j] = tmp;
        }
        return out;
    }

    // ── 제목 자동 줄이기 ────────────────────────────────────
    /* 제목이 길면 왼쪽 로고까지 밀고 들어온다. 로고가 놓인 자리를 피하도록
       글자 크기를 줄인다.

       원래 크기는 레이어 코멘트에 적어 둔다. 그래야 다음 회차에 짧은 제목이
       오면 원래 크기로 돌아온다 — 안 그러면 실행할 때마다 작아지기만 한다.

       회전이나 부모 연결이 걸린 레이어는 위치 계산이 맞지 않으므로
       계산에서 빼고 그냥 둔다. */
    var SIZE_TAG = "plpipe-base-size:";

    function transformOf(layer) {
        var tr = layer.property("ADBE Transform Group");
        return { pos: tr.property("ADBE Position").value,
                 anchor: tr.property("ADBE Anchor Point").value,
                 scale: tr.property("ADBE Scale").value };
    }

    function boxOf(layer) {
        var rect = layer.sourceRectAtTime(Math.max(0, layer.inPoint), false);
        var t = transformOf(layer);
        var sx = t.scale[0] / 100, sy = t.scale[1] / 100;
        var left = t.pos[0] + (rect.left - t.anchor[0]) * sx;
        var top = t.pos[1] + (rect.top - t.anchor[1]) * sy;
        return { left: left, top: top,
                 width: rect.width * Math.abs(sx),
                 height: rect.height * Math.abs(sy),
                 right: left + rect.width * Math.abs(sx),
                 bottom: top + rect.height * Math.abs(sy) };
    }

    function isRotated(layer) {
        try {
            if (layer.parent) return true;
            var r = layer.property("ADBE Transform Group")
                         .property("ADBE Rotate Z");
            if (r && r.value) return true;
        } catch (e) {}
        return false;
    }

    /* 메인 컴프에서 로고처럼 화면 한쪽에만 놓인 작은 레이어를 찾아,
       제목이 쓸 수 있는 가로 구간을 정한다. 화면을 꽉 채우는 배경이나
       영상 오버레이는 제외한다. */
    function freeBand(main) {
        var w = main.width, h = main.height;
        var left = 0, right = w, found = false;
        for (var i = 1; i <= main.layers.length; i++) {
            var layer = main.layers[i], kind = layerKind(layer);
            if (kind !== "still" && kind !== "footage" && kind !== "shape") continue;
            if (!layer.enabled || isRotated(layer)) continue;
            var box = null;
            try { box = boxOf(layer); } catch (e) { continue; }
            if (!box || !box.width) continue;
            if (box.width > w * 0.5 || box.height > h * 0.5) continue;   // 배경
            var center = box.left + box.width / 2;
            if (center < w / 2) {
                if (box.right > left) { left = box.right; found = true; }
            } else if (box.left < right) {
                right = box.left; found = true;
            }
        }
        var margin = w * 0.02;
        return { left: left + margin, right: right - margin, found: found };
    }

    function textProp(layer) {
        return layer.property("ADBE Text Properties").property("ADBE Text Document");
    }

    /* 곡 컴프 안 좌표를 메인 컴프 좌표로 옮긴다. 프리컴프 레이어에 걸린
       위치·앵커·스케일만 반영한다. */
    function bandBox(textLayer, slotLayer) {
        var inner = boxOf(textLayer);
        var t = transformOf(slotLayer);
        var sx = t.scale[0] / 100;
        var left = t.pos[0] + (inner.left - t.anchor[0]) * sx;
        var width = inner.width * Math.abs(sx);
        return { left: left, right: left + width, width: width };
    }

    function overflowOf(textLayer, slotLayer, band) {
        var box = bandBox(textLayer, slotLayer), out = 0;
        if (box.left < band.left) out += band.left - box.left;
        if (box.right > band.right) out += box.right - band.right;
        return { over: out, width: box.width };
    }

    /* 원래 크기로 되돌린 뒤, 구간 밖으로 나간 만큼 줄여 다시 잰다.
       글자 폭은 크기에 거의 비례하므로 두세 번이면 수렴한다. */
    function fitTitle(textLayer, slotLayer, band) {
        if (isRotated(textLayer)) return null;
        var prop, doc;
        try { prop = textProp(textLayer); doc = prop.value; } catch (e) { return null; }
        var current = doc.fontSize;
        if (!current || !isFinite(current)) return null;

        var note = String(textLayer.comment || "");
        var m = new RegExp(SIZE_TAG + "([0-9.]+)").exec(note);
        var base = m ? parseFloat(m[1]) : current;
        if (!m) {
            try {
                textLayer.comment = (note ? note + " " : "") + SIZE_TAG + current;
            } catch (e2) {}
        }
        /* 글자마다 서식이 다른 텍스트처럼 크기를 못 바꾸는 레이어가 있다.
           여기서 터지면 나머지 곡까지 멈추므로 그냥 두고 넘어간다. */
        var size = base, floor = base * 0.35, state0;
        try {
            if (doc.fontSize !== base) {          // 늘 원래 크기에서 다시 잰다
                doc.fontSize = base;
                prop.setValue(doc);
            }
            state0 = overflowOf(textLayer, slotLayer, band);
            if (state0.over <= 0.5) return null;      // 원래 크기로 들어간다
            for (var guard = 0; guard < 12 && state0.over > 0.5 && size > floor; guard++) {
                var factor = (state0.width - state0.over) / state0.width;
                if (!(factor > 0) || factor > 0.995) factor = 0.97;
                size = Math.max(floor, size * factor);
                doc = prop.value;
                doc.fontSize = size;
                prop.setValue(doc);
                state0 = overflowOf(textLayer, slotLayer, band);
            }
        } catch (e3) { return null; }
        return { base: base, size: size, fits: state0.over <= 0.5 };
    }

    // ── 프로젝트 훑기 ───────────────────────────────────────
    function allComps() {
        var out = [];
        for (var i = 1; i <= app.project.numItems; i++) {
            if (app.project.item(i) instanceof CompItem) out.push(app.project.item(i));
        }
        return out;
    }

    function guessMain(comps) {
        var best = null, bestCount = -1;
        for (var i = 0; i < comps.length; i++) {
            var count = 0;
            for (var j = 1; j <= comps[i].layers.length; j++) {
                if (comps[i].layers[j].source instanceof CompItem) count++;
            }
            if (count > bestCount) { bestCount = count; best = comps[i]; }
        }
        return best;
    }

    /* 메인 컴프에 놓인 프리컴프들을 시작 시각 순으로. 각 항목에
       길이·꺼짐 여부·제목 텍스트 유무를 같이 담아 곡인지 판단에 쓴다. */
    function slotsOf(main) {
        var rows = [];
        for (var i = 1; i <= main.layers.length; i++) {
            var layer = main.layers[i];
            if (!(layer.source instanceof CompItem)) continue;
            var comp = layer.source;
            var hasText = false, imageLayer = null;
            for (var j = 1; j <= comp.layers.length; j++) {
                var kind = layerKind(comp.layers[j]);
                if (kind === "text") hasText = true;
                if (!imageLayer && (kind === "still" || kind === "footage")) {
                    imageLayer = comp.layers[j];
                }
            }
            rows.push({
                comp: comp,
                layer: layer,
                start: layer.startTime,
                span: layer.outPoint - layer.inPoint,
                enabled: layer.enabled,
                hasText: hasText,
                hasImage: !!imageLayer
            });
        }
        rows.sort(function (a, b) { return a.start - b.start; });
        return rows;
    }

    function median(values) {
        if (!values.length) return 0;
        var copy = values.slice();
        copy.sort(function (a, b) { return a - b; });
        var mid = Math.floor(copy.length / 2);
        return copy.length % 2 ? copy[mid] : (copy[mid - 1] + copy[mid]) / 2;
    }

    /* 곡일 가능성이 낮은 항목을 표시한다. 인트로는 훨씬 짧게 놓여 있고,
       안 쓰는 잔재는 꺼져 있으며, 곡 컴프에는 보통 제목 텍스트가 있다. */
    function markSongs(rows) {
        var spans = [], i;
        for (i = 0; i < rows.length; i++) {
            if (rows[i].span > 0) spans.push(rows[i].span);
        }
        var middle = median(spans);
        var withText = 0;
        for (i = 0; i < rows.length; i++) if (rows[i].hasText) withText++;

        for (i = 0; i < rows.length; i++) {
            var row = rows[i];
            var why = [];
            if (spans.length >= 3 && middle > 0 && row.span < middle * 0.4) {
                why.push("길이 " + Math.round(row.span) + "초");
            }
            if (!row.enabled) why.push("꺼져 있음");
            if (withText >= rows.length * 0.6 && !row.hasText) why.push("제목 없음");
            if (!row.hasImage) why.push("이미지 레이어 없음");
            row.isSong = why.length === 0;
            row.why = why.join(", ");
        }
        return rows;
    }

    /* 곡이 아닌 컴프(인트로 등)를 AE 설정에 기억해 둔다. 트랙리스트
       스크립트와 같은 저장소를 쓰므로 한쪽에서 지정하면 양쪽에 적용된다. */
    var SETTINGS_SECTION = "plpipe";
    var SETTINGS_KEY = "notSongs";

    function loadExcluded() {
        try {
            if (!app.settings.haveSetting(SETTINGS_SECTION, SETTINGS_KEY)) return [];
            var raw = app.settings.getSetting(SETTINGS_SECTION, SETTINGS_KEY);
            return raw ? raw.split("\n") : [];
        } catch (e) { return []; }
    }

    function saveExcluded(names) {
        try {
            app.settings.saveSetting(SETTINGS_SECTION, SETTINGS_KEY, names.join("\n"));
        } catch (e) {}
    }

    function nameIn(list, name) {
        for (var i = 0; i < list.length; i++) if (list[i] === name) return true;
        return false;
    }

    // ── 시작 ────────────────────────────────────────────────
    if (!app.project || app.project.numItems === 0) {
        alert("먼저 After Effects 에서 프로젝트를 열어 주세요.");
        return;
    }
    var comps = allComps();
    if (!comps.length) {
        alert("이 프로젝트에는 컴포지션이 없습니다.");
        return;
    }

    var mainGuess = guessMain(comps);
    var state = {
        main: mainGuess,
        rows: markSongs(slotsOf(mainGuess)),
        images: []
    };

    // ── 창 만들기 ───────────────────────────────────────────
    var VERSION = "v4";   // 창 제목에 표시된다. 파일을 바꿨는지 확인용.
    var win = new Window("dialog", "이미지 넣기  " + VERSION);
    win.orientation = "column";
    win.alignChildren = ["fill", "top"];
    win.preferredSize = [720, 560];
    win.margins = 16;

    // 메인 컴프 고르기
    var topRow = win.add("group");
    topRow.add("statictext", undefined, "메인 컴프:");
    var compNames = [];
    for (var c = 0; c < comps.length; c++) compNames.push(comps[c].name);
    var compPick = topRow.add("dropdownlist", undefined, compNames);
    compPick.selection = 0;
    for (var k = 0; k < comps.length; k++) {
        if (comps[k] === mainGuess) compPick.selection = k;
    }
    compPick.preferredSize.width = 260;

    // 이미지 폴더 고르기
    var folderRow = win.add("group");
    folderRow.alignChildren = ["fill", "center"];
    folderRow.add("statictext", undefined, "이미지 폴더:");
    var folderText = folderRow.add("edittext", undefined, "");
    folderText.preferredSize.width = 420;
    folderText.enabled = false;
    var browse = folderRow.add("button", undefined, "찾아보기…");

    win.add("statictext", undefined,
        "아래에서 곡 컴프만 선택하세요. 선택된 것에 이미지가 순서대로 들어갑니다.");

    var list = win.add("listbox", undefined, [], {
        multiselect: true,
        numberOfColumns: 5,
        showHeaders: true,
        columnTitles: ["#", "컴프", "길이", "들어갈 이미지", "비고"],
        columnWidths: [30, 220, 60, 220, 140]
    });
    list.preferredSize.height = 300;

    var buttons = win.add("group");
    var selectAll = buttons.add("button", undefined, "전체 선택");
    var selectGuess = buttons.add("button", undefined, "추천대로");
    var selectNone = buttons.add("button", undefined, "선택 해제");
    var shuffleBtn = buttons.add("button", undefined, "이미지 섞기");
    var orderBtn = buttons.add("button", undefined, "원래 순서");

    var options = win.add("group");
    var introBox = options.add("checkbox", undefined, "인트로에도 1번 이미지");
    var titleBox = options.add("checkbox", undefined, "곡 제목도 파일명으로 바꾸기");
    var shrinkBox = options.add("checkbox", undefined, "긴 제목 줄이기");
    introBox.value = true;
    titleBox.value = false;
    shrinkBox.value = true;

    var status = win.add("statictext", undefined, "");
    status.characters = 80;

    var actions = win.add("group");
    actions.alignment = ["fill", "bottom"];
    actions.alignChildren = ["right", "center"];
    var spacer = actions.add("statictext", undefined, "");
    spacer.alignment = ["fill", "center"];
    var applyBtn = actions.add("button", undefined, "적용", { name: "ok" });
    var closeBtn = actions.add("button", undefined, "닫기", { name: "cancel" });

    // ── 표 그리기 ───────────────────────────────────────────
    function selectedIndexes() {
        var out = [];
        var sel = list.selection;
        if (!sel) return out;
        if (!(sel instanceof Array)) sel = [sel];
        for (var i = 0; i < sel.length; i++) out.push(sel[i].index);
        out.sort(function (a, b) { return a - b; });
        return out;
    }

    function redraw(keepSelection) {
        var wanted = keepSelection || [];
        state.settingSelection = true;
        list.removeAll();
        for (var i = 0; i < state.rows.length; i++) {
            var row = state.rows[i];
            var item = list.add("item", String(i + 1));
            item.subItems[0].text = row.comp.name;
            item.subItems[1].text = Math.round(row.span) + "초";
            item.subItems[2].text = "";
            item.subItems[3].text = row.why;
        }
        var sel = [];
        for (var j = 0; j < wanted.length; j++) {
            if (wanted[j] < list.items.length) sel.push(list.items[wanted[j]]);
        }
        list.selection = sel;
        state.settingSelection = false;
        refreshMapping();
    }

    /* "인트로에도 1번 이미지" 를 켜면 첫 곡과 같은 그림이 인트로에 들어간다.
       인트로는 선택에서 빠진 컴프 중 타임라인에서 가장 앞선 것으로 본다.
       꺼진 잔재 컴프는 눈에 보이지 않으므로 후보에서 뺀다. */
    function introIndex() {
        var chosen = selectedIndexes(), i, j;
        for (i = 0; i < state.rows.length; i++) {
            var inSel = false;
            for (j = 0; j < chosen.length; j++) if (chosen[j] === i) inSel = true;
            if (!inSel && state.rows[i].enabled) return i;
        }
        return -1;
    }

    function fileLabel(file) {
        return file ? decodeURI(file.displayName || file.name) : "";
    }

    function refreshMapping() {
        var chosen = selectedIndexes();
        for (var i = 0; i < list.items.length; i++) {
            list.items[i].subItems[2].text = "";
        }
        for (var n = 0; n < chosen.length; n++) {
            var file = state.images[n];
            list.items[chosen[n]].subItems[2].text =
                file ? fileLabel(file) : "(이미지 부족)";
        }
        var intro = introBox.value && state.images.length ? introIndex() : -1;
        if (intro >= 0 && intro < list.items.length) {
            list.items[intro].subItems[2].text =
                fileLabel(state.images[0]) + "  (인트로)";
        }
        var msg = "선택한 컴프 " + chosen.length + "개 / 전체 "
                  + state.rows.length + "개 · 이미지 " + state.images.length + "장";
        if (state.usedMemory) msg += "   (지난번 제외 설정을 기억했습니다)";
        if (state.images.length && chosen.length !== state.images.length) {
            msg += "   ← 개수가 다릅니다";
        }
        status.text = msg;
        applyBtn.enabled = chosen.length > 0 && state.images.length > 0;
    }

    /* 폴더에 이미지가 몇 장인지는 확실한 정보다. 구조로 추측한 결과가
       그 장수와 안 맞으면 장수를 더 믿는다. */
    function guessSelection() {
        var i;

        /* 1순위: 지난번에 "곡 아님" 으로 지정해 둔 컴프. 이름으로 기억하므로
           길이가 어떻든 정확히 그것만 빠진다. */
        var remembered = loadExcluded(), matched = 0;
        for (i = 0; i < state.rows.length; i++) {
            if (nameIn(remembered, state.rows[i].comp.name)) matched++;
        }
        if (matched) {
            var kept0 = [];
            for (i = 0; i < state.rows.length; i++) {
                if (!nameIn(remembered, state.rows[i].comp.name)) kept0.push(i);
            }
            state.usedMemory = true;
            if (!state.images.length || kept0.length === state.images.length) {
                return kept0;
            }
        }
        state.usedMemory = false;

        var guessed = [];
        for (i = 0; i < state.rows.length; i++) {
            if (state.rows[i].isSong) guessed.push(i);
        }
        var want = state.images.length;
        if (!want || guessed.length === want) return guessed;

        var total = state.rows.length, all = [], i2;
        for (i2 = 0; i2 < total; i2++) all.push(i2);
        if (total <= want) return all;           // 모자라면 전부 (개수 경고가 뜬다)

        /* 남는 개수만큼 짧은 것부터 뺀다. 인트로·아웃트로·잔재는 곡보다
           짧게 놓여 있기 때문이다. 꺼진 레이어는 무조건 먼저 뺀다. */
        var order = [];
        for (i2 = 0; i2 < total; i2++) order.push(i2);
        order.sort(function (a, b) {
            var ea = state.rows[a].enabled ? 1 : 0;
            var eb = state.rows[b].enabled ? 1 : 0;
            if (ea !== eb) return ea - eb;       // 꺼진 것 먼저
            return state.rows[a].span - state.rows[b].span;
        });
        var dropped = {};
        for (i2 = 0; i2 < total - want; i2++) dropped[order[i2]] = true;
        var kept = [];
        for (i2 = 0; i2 < total; i2++) if (!dropped[i2]) kept.push(i2);
        return kept;
    }

    // ── 이벤트 ──────────────────────────────────────────────
    compPick.onChange = function () {
        state.main = comps[compPick.selection.index];
        state.rows = markSongs(slotsOf(state.main));
        redraw(guessSelection());
    };

    browse.onClick = function () {
        var folder = Folder.selectDialog("이미지가 들어 있는 폴더를 고르세요");
        if (!folder) return;
        folderText.text = folder.fsName;
        var files = folder.getFiles(function (f) {
            return (f instanceof File) && IMAGE_EXT.test(f.name);
        });
        state.images = sortImages(files);
        state.ordered = state.images.slice();   // "원래 순서" 로 되돌릴 기준
        /* 손대기 전이라면 장수를 알게 된 지금 다시 추천한다. */
        if (state.touched) refreshMapping();
        else redraw(guessSelection());
    };

    /* 누를 때마다 다시 섞인다. 표에서 어느 곡에 어느 그림이 갈지 바로
       보이므로, 마음에 들 때까지 눌러 보고 적용하면 된다. */
    shuffleBtn.onClick = function () {
        if (!state.images.length) return;
        state.images = shuffled(state.images);
        refreshMapping();
    };
    orderBtn.onClick = function () {
        if (!state.ordered) return;
        state.images = state.ordered.slice();
        refreshMapping();
    };
    introBox.onClick = refreshMapping;

    list.onChange = function () {
        if (!state.settingSelection) state.touched = true;
        refreshMapping();
    };
    selectAll.onClick = function () {
        var all = [];
        for (var i = 0; i < state.rows.length; i++) all.push(i);
        state.touched = true;
        redraw(all);
    };
    selectGuess.onClick = function () {
        state.touched = false;
        redraw(guessSelection());
    };
    selectNone.onClick = function () {
        state.touched = true;
        redraw([]);
    };

    applyBtn.onClick = function () {
        var chosen = selectedIndexes();
        if (chosen.length > state.images.length) {
            if (!confirm("컴프 " + chosen.length + "개에 이미지가 "
                         + state.images.length + "장뿐입니다.\n"
                         + "앞에서부터 " + state.images.length
                         + "개만 바꿀까요?")) return;
        }

        // 이번에 곡이 아니라고 본 컴프를 기억해 둔다.
        var excludedNames = [];
        for (var x = 0; x < state.rows.length; x++) {
            var inSel = false;
            for (var y = 0; y < chosen.length; y++) if (chosen[y] === x) inSel = true;
            if (!inSel) excludedNames.push(state.rows[x].comp.name);
        }
        saveExcluded(excludedNames);

        var band = freeBand(state.main);
        var report = { done: 0, failed: [], shrunk: 0, tooLong: [] };

        /* 컴프 하나에 이미지 한 장을 넣는다. 인트로에도 같은 함수를 쓰므로
           제목만 안 바꾸도록 setTitle 로 나눠 둔다. */
        function swapInto(row, file, setTitle) {
            var target = null, titleLayer = null;
            for (var j = 1; j <= row.comp.layers.length; j++) {
                var kind = layerKind(row.comp.layers[j]);
                if (!target && (kind === "still" || kind === "footage")) {
                    target = row.comp.layers[j];
                }
                if (!titleLayer && kind === "text") titleLayer = row.comp.layers[j];
            }
            if (!target) {
                report.failed.push(row.comp.name + " (이미지 레이어 없음)");
                return;
            }

            var opts = new ImportOptions(file);
            if (opts.canImportAs(ImportAsType.FOOTAGE)) {
                opts.importAs = ImportAsType.FOOTAGE;
            }
            var footage = app.project.importFile(opts);
            target.replaceSource(footage, false);

            /* 스케일에 키프레임이나 익스프레션이 걸려 있으면(켄번즈 같은
               모션) 건드리지 않는다. 아니면 컴프를 꽉 채우도록 맞춘다. */
            var scale = target.property("ADBE Transform Group")
                              .property("ADBE Scale");
            if (scale.numKeys === 0 && !scale.expressionEnabled
                    && footage.width && footage.height) {
                var factor = Math.max(row.comp.width / footage.width,
                                      row.comp.height / footage.height);
                scale.setValue([factor * 100, factor * 100]);
            }

            if (setTitle && titleBox.value && titleLayer) {
                var name = baseName(file).replace(/^\s*\d{1,3}\s*[-_.]?\s*/, "");
                var prop = titleLayer.property("ADBE Text Properties")
                                     .property("ADBE Text Document");
                var doc = prop.value;
                doc.text = name;
                prop.setValue(doc);
            }
            if (shrinkBox.value && titleLayer) {
                var fit = fitTitle(titleLayer, row.layer, band);
                if (fit) {
                    report.shrunk++;
                    if (!fit.fits) report.tooLong.push(row.comp.name);
                }
            }
            report.done++;
        }

        var intro = introBox.value ? introIndex() : -1;

        app.beginUndoGroup("이미지 넣기");
        try {
            for (var n = 0; n < chosen.length && n < state.images.length; n++) {
                swapInto(state.rows[chosen[n]], state.images[n], true);
            }
            // 인트로는 첫 곡과 같은 그림을 쓰되 제목은 건드리지 않는다.
            if (intro >= 0 && state.images.length) {
                swapInto(state.rows[intro], state.images[0], false);
            }
        } catch (e) {
            app.endUndoGroup();
            alert("중간에 오류가 났습니다:\n" + e.toString()
                  + "\n\nCtrl+Z 를 누르면 되돌릴 수 있습니다.");
            return;
        }
        app.endUndoGroup();

        var msg = report.done + "개 컴프의 이미지를 바꿨습니다.";
        if (intro >= 0) {
            msg += "\n인트로(" + state.rows[intro].comp.name
                   + ")에는 1번 이미지를 같이 넣었습니다.";
        }
        if (report.shrunk) msg += "\n긴 제목 " + report.shrunk + "개를 줄였습니다.";
        if (report.tooLong.length) {
            msg += "\n\n많이 줄여도 로고에 닿는 제목:\n  "
                   + report.tooLong.join("\n  ");
        }
        if (report.failed.length) {
            msg += "\n\n건너뛴 것:\n  " + report.failed.join("\n  ");
        }
        msg += "\n\n마음에 안 들면 Ctrl+Z 로 되돌릴 수 있습니다.";
        alert(msg);
        win.close();
    };

    closeBtn.onClick = function () { win.close(); };

    redraw(guessSelection());
    win.center();
    win.show();
})();
