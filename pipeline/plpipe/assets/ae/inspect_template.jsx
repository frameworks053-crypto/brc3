/*
 * plpipe — AE 템플릿 구조 덤프
 *
 * PLPIPE_JOB.project 의 .aep 를 열어 컴프/레이어 이름을 JSON 으로 뽑는다.
 * 이 결과를 보고 config.toml 의 [ae] 이름 매핑을 채우면 된다.
 *
 * ExtendScript 는 ES3 수준이라 JSON, forEach, let/const 를 쓸 수 없다.
 */

(function () {
    // ── 최소 JSON 직렬화 ────────────────────────────────────
    function esc(s) {
        s = String(s);
        var out = "";
        for (var i = 0; i < s.length; i++) {
            var c = s.charAt(i);
            var code = s.charCodeAt(i);
            if (c === '"') out += '\\"';
            else if (c === "\\") out += "\\\\";
            else if (c === "\n") out += "\\n";
            else if (c === "\r") out += "\\r";
            else if (c === "\t") out += "\\t";
            else if (code < 0x20) out += "\\u" + ("000" + code.toString(16)).slice(-4);
            else out += c;
        }
        return '"' + out + '"';
    }

    function ser(v, indent) {
        var pad = new Array(indent + 1).join("  ");
        var inner = new Array(indent + 2).join("  ");
        var i, parts;
        if (v === null || v === undefined) return "null";
        if (typeof v === "number") return isFinite(v) ? String(v) : "null";
        if (typeof v === "boolean") return v ? "true" : "false";
        if (v instanceof Array) {
            if (!v.length) return "[]";
            parts = [];
            for (i = 0; i < v.length; i++) parts.push(inner + ser(v[i], indent + 1));
            return "[\n" + parts.join(",\n") + "\n" + pad + "]";
        }
        if (typeof v === "object") {
            parts = [];
            for (var k in v) {
                if (v.hasOwnProperty(k)) {
                    parts.push(inner + esc(k) + ": " + ser(v[k], indent + 1));
                }
            }
            if (!parts.length) return "{}";
            return "{\n" + parts.join(",\n") + "\n" + pad + "}";
        }
        return esc(v);
    }

    function layerKind(layer) {
        if (layer instanceof TextLayer) return "text";
        if (layer instanceof ShapeLayer) return "shape";
        if (layer instanceof CameraLayer) return "camera";
        if (layer instanceof LightLayer) return "light";
        if (layer.nullLayer) return "null";
        if (layer.source instanceof CompItem) return "precomp";
        if (layer.source instanceof FootageItem) {
            var main = layer.source.mainSource;
            if (main instanceof SolidSource) return "solid";
            if (layer.source.hasAudio && !layer.source.hasVideo) return "audio";
            // 선택자(@still / @footage)와 같은 이름을 써야 헷갈리지 않는다.
            if (layer.source.hasVideo && layer.source.duration === 0) return "still";
            return "footage";
        }
        return "other";
    }

    function templateNames() {
        // 렌더 설정 / 출력 모듈 템플릿 목록은 큐에 임시 항목을 넣어야 읽을 수 있다.
        var result = { render_settings: [], output_modules: [] };
        var temp = null;
        try {
            temp = app.project.items.addComp("__plpipe_probe", 4, 4, 1, 1, 1);
            var rq = app.project.renderQueue.items.add(temp);
            result.render_settings = rq.templates;
            result.output_modules = rq.outputModule(1).templates;
            rq.remove();
        } catch (e) {
            result.error = String(e);
        }
        if (temp) { try { temp.remove(); } catch (e2) {} }
        return result;
    }

    var report = { project: PLPIPE_JOB.project, comps: [], footage_count: 0 };

    app.open(new File(PLPIPE_JOB.project));

    var items = app.project.items;
    for (var i = 1; i <= items.length; i++) {
        var item = items[i];
        if (!(item instanceof CompItem)) {
            if (item instanceof FootageItem) report.footage_count++;
            continue;
        }
        var comp = {
            name: item.name,
            width: item.width,
            height: item.height,
            fps: item.frameRate,
            duration: Math.round(item.duration * 1000) / 1000,
            layers: []
        };
        for (var j = 1; j <= item.layers.length; j++) {
            var layer = item.layers[j];
            var entry = { index: j, name: layer.name, kind: layerKind(layer) };
            if (layer.source && layer.source.name) entry.source = layer.source.name;
            if (layer instanceof AVLayer) {
                var scale = layer.property("ADBE Transform Group")
                                 .property("ADBE Scale");
                entry.scale_keyframed = scale.numKeys > 0 || !!scale.expressionEnabled;
            }
            comp.layers.push(entry);
        }
        report.comps.push(comp);
    }

    report.templates = templateNames();

    var out = new File(PLPIPE_JOB.save_as);
    out.encoding = "UTF-8";
    out.open("w");
    out.write(ser(report, 0));
    out.close();

    $.writeln("plpipe: 구조 덤프 완료 → " + PLPIPE_JOB.save_as);
    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
})();
