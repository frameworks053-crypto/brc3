/*
 * plpipe — 템플릿 구조 리포트 (단독 실행용)
 *
 * 설치도 설정도 필요 없습니다. After Effects 에서 프로젝트를 열어둔 뒤
 * File > Scripts > Run Script File... 로 이 파일을 실행하세요.
 * (열어둔 프로젝트가 없으면 파일 선택 창이 뜹니다)
 *
 * 컴프·레이어 구조를 텍스트로 뽑아서
 *   1) 창에 띄우고 (Ctrl+A / Ctrl+C 로 복사 가능)
 *   2) .aep 옆에 plpipe-template-report.txt 로 저장합니다
 *
 * 이 리포트를 붙여넣으면 config.toml 의 [ae] 이름 매핑을 맞출 수 있습니다.
 * 프로젝트를 읽기만 하고 아무것도 바꾸지 않습니다.
 *
 * ExtendScript 는 ES3 수준이라 let/const/forEach/JSON 을 쓸 수 없습니다.
 */

(function () {
    var NL = "\n";
    var out = [];

    function w(line) { out.push(line === undefined ? "" : String(line)); }

    function pad(s, n) {
        s = String(s);
        while (s.length < n) s += " ";
        return s;
    }

    function round(v, digits) {
        var f = Math.pow(10, digits || 2);
        return Math.round(v * f) / f;
    }

    // ── 프로젝트 확보 ───────────────────────────────────────
    if (!app.project || app.project.numItems === 0) {
        var picked = File.openDialog("구조를 확인할 .aep 파일을 고르세요", "*.aep");
        if (!picked) return;
        app.open(picked);
    }
    var proj = app.project;
    var projFile = proj.file;

    // ── 레이어 종류 판별 ────────────────────────────────────
    function layerKind(layer) {
        if (layer instanceof TextLayer) return "text";
        if (layer instanceof ShapeLayer) return "shape";
        if (layer instanceof CameraLayer) return "camera";
        if (layer instanceof LightLayer) return "light";
        if (layer.nullLayer) return "null";
        if (layer.adjustmentLayer) return "adjustment";
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

    // 트랜스폼 속성에 키프레임/익스프레션이 걸렸는지. 걸려 있으면 plpipe 가
    // 건드리지 않으므로(모션 보존) 어떤 게 걸렸는지 아는 게 중요하다.
    function animatedProps(layer) {
        if (!(layer instanceof AVLayer) && !(layer instanceof TextLayer)) return "";
        var names = ["ADBE Anchor Point", "ADBE Position", "ADBE Scale",
                     "ADBE Rotate Z", "ADBE Opacity"];
        var labels = ["앵커", "위치", "스케일", "회전", "불투명도"];
        var hits = [];
        var group;
        try { group = layer.property("ADBE Transform Group"); } catch (e) { return ""; }
        if (!group) return "";
        for (var i = 0; i < names.length; i++) {
            var p = null;
            try { p = group.property(names[i]); } catch (e2) {}
            if (!p) continue;
            if (p.numKeys > 0) hits.push(labels[i] + " 키프레임");
            else if (p.expressionEnabled) hits.push(labels[i] + " 익스프레션");
        }
        return hits.length ? "  [" + hits.join(", ") + "]" : "";
    }

    function textOf(layer) {
        if (!(layer instanceof TextLayer)) return "";
        try {
            var t = layer.property("ADBE Text Properties")
                         .property("ADBE Text Document").value.text;
            t = String(t).replace(/[\r\n]+/g, " / ");
            if (t.length > 40) t = t.substring(0, 40) + "…";
            return '  "' + t + '"';
        } catch (e) { return ""; }
    }

    // ── 컴프 사용처 집계 (어떤 컴프가 어디에 몇 번 쓰이는지) ──
    var comps = [];
    var i, j;
    for (i = 1; i <= proj.numItems; i++) {
        if (proj.item(i) instanceof CompItem) comps.push(proj.item(i));
    }

    var usage = {};   // 컴프 이름 -> [ "MAIN×13", ... ]
    for (i = 0; i < comps.length; i++) {
        var parent = comps[i];
        var counts = {};
        for (j = 1; j <= parent.layers.length; j++) {
            var src = parent.layers[j].source;
            if (src instanceof CompItem) {
                counts[src.name] = (counts[src.name] || 0) + 1;
            }
        }
        for (var childName in counts) {
            if (!counts.hasOwnProperty(childName)) continue;
            if (!usage[childName]) usage[childName] = [];
            usage[childName].push(parent.name + "×" + counts[childName]);
        }
    }

    // ── 리포트 작성 ─────────────────────────────────────────
    w("=========================================");
    w(" plpipe 템플릿 구조 리포트");
    w("=========================================");
    w("프로젝트 : " + (projFile ? projFile.fsName : "(저장 안 된 프로젝트)"));
    w("AE 버전  : " + app.version);
    w("컴프 수  : " + comps.length);
    w();

    w("=== 컴프 목록 ===");
    w();
    for (i = 0; i < comps.length; i++) {
        var comp = comps[i];
        var used = usage[comp.name];
        var where = used ? "다른 컴프에서 사용: " + used.join(", ") : "최상위 (어디에도 안 쓰임)";
        w("[" + (i + 1) + "] " + comp.name);
        w("    " + comp.width + "x" + comp.height +
          " @" + round(comp.frameRate, 3) + "fps" +
          "  길이 " + round(comp.duration, 3) + "s" +
          "  레이어 " + comp.layers.length + "개");
        w("    " + where);
        for (j = 1; j <= comp.layers.length; j++) {
            var layer = comp.layers[j];
            var srcName = "";
            if (layer.source && layer.source.name) srcName = "  src=" + layer.source.name;
            w("      " + pad(j + ".", 4) + pad(layer.name, 24) +
              pad(layerKind(layer), 11) + srcName +
              textOf(layer) + animatedProps(layer) +
              (layer.enabled ? "" : "  [꺼짐]"));
        }
        w();
    }

    // ── 푸티지 ──────────────────────────────────────────────
    w("=== 푸티지 ===");
    var footageCount = 0;
    for (i = 1; i <= proj.numItems; i++) {
        var item = proj.item(i);
        if (!(item instanceof FootageItem)) continue;
        if (item.mainSource instanceof SolidSource) continue;
        footageCount++;
        if (footageCount > 30) continue;
        w("    " + pad(item.name, 32) + item.width + "x" + item.height +
          (item.duration ? "  " + round(item.duration, 2) + "s" : "  (스틸)"));
    }
    if (footageCount === 0) w("    (없음)");
    if (footageCount > 30) w("    … 외 " + (footageCount - 30) + "개");
    w();

    // ── 렌더 템플릿 이름 ────────────────────────────────────
    w("=== 렌더 설정 / 출력 모듈 템플릿 ===");
    var probe = null, rqItem = null;
    try {
        probe = proj.items.addComp("__plpipe_probe", 4, 4, 1, 1, 1);
        rqItem = app.project.renderQueue.items.add(probe);
        w("    렌더 설정 : " + rqItem.templates.join(", "));
        w("    출력 모듈 : " + rqItem.outputModule(1).templates.join(", "));
    } catch (e) {
        w("    (읽지 못했습니다: " + e.toString() + ")");
    }
    // 흔적을 남기지 않는다.
    if (rqItem) { try { rqItem.remove(); } catch (e3) {} }
    if (probe) { try { probe.remove(); } catch (e4) {} }
    w();

    // ── 추천 설정 (단순 휴리스틱) ───────────────────────────
    w("=== config.toml 추천값 (확인 필요) ===");
    var mainGuess = "", slotGuess = "", imageGuess = "", titleGuess = "", audioGuess = "";
    var longest = 0, mostUsed = 0;
    for (i = 0; i < comps.length; i++) {
        // 최상위이면서 가장 긴 컴프를 MAIN 후보로.
        if (!usage[comps[i].name] && comps[i].duration > longest) {
            longest = comps[i].duration;
            mainGuess = comps[i].name;
        }
        // 다른 컴프에서 가장 많이 쓰이는 컴프를 SLOT 후보로.
        var total = 0;
        var list = usage[comps[i].name] || [];
        for (j = 0; j < list.length; j++) {
            total += parseInt(list[j].split("×")[1], 10) || 0;
        }
        if (total > mostUsed) { mostUsed = total; slotGuess = comps[i].name; }
    }
    // 슬롯 후보 안에서 이미지/제목 레이어 찾기.
    for (i = 0; i < comps.length; i++) {
        if (comps[i].name !== slotGuess) continue;
        for (j = 1; j <= comps[i].layers.length; j++) {
            var L = comps[i].layers[j];
            var k = layerKind(L);
            if (!imageGuess && (k === "still" || k === "footage")) imageGuess = L.name;
            if (!titleGuess && k === "text") titleGuess = L.name;
        }
    }
    for (i = 0; i < comps.length; i++) {
        if (comps[i].name !== mainGuess) continue;
        for (j = 1; j <= comps[i].layers.length; j++) {
            if (layerKind(comps[i].layers[j]) === "audio") {
                audioGuess = comps[i].layers[j].name;
                break;
            }
        }
    }
    w("[ae]");
    w('main_comp   = "' + mainGuess + '"');
    w('slot_comp   = "' + slotGuess + '"');
    w('image_layer = "' + imageGuess + '"');
    w('title_layer = "' + titleGuess + '"');
    w('audio_layer = "' + audioGuess + '"');
    w();
    w("(휴리스틱 추정치입니다. 위 컴프 목록을 보고 직접 확인하세요.)");

    var report = out.join(NL);

    // ── 파일로 저장 ─────────────────────────────────────────
    var savedTo = "";
    try {
        var dir = projFile ? projFile.parent : Folder.desktop;
        var file = new File(dir.fsName + "/plpipe-template-report.txt");
        file.encoding = "UTF-8";
        if (file.open("w")) {
            file.write(report);
            file.close();
            savedTo = file.fsName;
        }
    } catch (e5) {}

    // ── 화면에 띄우기 (복사할 수 있게) ──────────────────────
    var win = new Window("dialog", "plpipe 템플릿 구조 리포트");
    win.orientation = "column";
    win.alignChildren = ["fill", "fill"];
    win.preferredSize = [760, 560];

    var head = win.add("statictext", undefined,
        savedTo ? "저장됨: " + savedTo : "파일 저장에 실패했습니다. 아래 내용을 복사하세요.");
    head.characters = 90;

    var box = win.add("edittext", undefined, report,
                      { multiline: true, scrolling: true, readonly: false });
    box.alignment = ["fill", "fill"];

    var row = win.add("group");
    row.alignment = ["fill", "bottom"];
    var hint = row.add("statictext", undefined,
        "본문을 클릭하고 Ctrl+A (Mac: Cmd+A) → Ctrl+C 로 전체 복사");
    hint.alignment = ["fill", "center"];
    var close = row.add("button", undefined, "닫기", { name: "ok" });
    close.onClick = function () { win.close(); };

    box.active = true;
    win.show();
})();
