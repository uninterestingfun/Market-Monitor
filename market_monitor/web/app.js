/* 市场监测前端：轮询快照、增量更新卡片、ECharts 蜡烛图 */
(() => {
  "use strict";

  const UP = "#ff5468";
  const DOWN = "#19c48a";
  const $ = (sel) => document.querySelector(sel);

  const state = {
    data: null,
    selected: "sh000001",
    range: "1Y",
    mode: "kline",
    intervalMs: 4000,
    timer: null,
    chart: null,
    chartKey: "",
    intradayAt: 0,
    cardRefs: new Map(),
    heroRefs: new Map(),
    tickerItems: [],
    tickerBuilt: false,
    building: false,
    downloadTimer: null,
    lastError: 0,
  };

  /* ─────────────────────────── 格式化 ─────────────────────────── */
  const num = (v, d = 2) =>
    v == null || Number.isNaN(v)
      ? "—"
      : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

  const signed = (v, d = 2) =>
    v == null ? "—" : (Math.abs(v) < 0.5 * Math.pow(10, -d) ? "" : v > 0 ? "+" : "") + num(v, d);

  const pctText = (p) =>
    p == null ? "—" : Math.abs(p) < 0.005 ? "0.00%" : (p > 0 ? "+" : "") + p.toFixed(2) + "%";

  const tone = (v) => (v == null || Math.abs(v) < 1e-9 ? "flat" : v > 0 ? "up" : "down");

  const colorOf = (v) => (v == null || Math.abs(v) < 1e-9 ? "#8b95a9" : v > 0 ? UP : DOWN);

  /** 方向判断值：利差用绝对变动（bp），其余用涨跌幅 */
  const dirOf = (a) => (a && a.kind === "spread" ? a.change : a && a.pct);

  function deltaText(a) {
    if (!a) return "—";
    if (a.kind === "spread") return a.change == null ? "—" : (a.change * 100).toFixed(1) + " bp";
    return pctText(a.pct);
  }

  function fmtVol(v) {
    if (v == null) return "—";
    if (v >= 1e8) return (v / 1e8).toFixed(2) + " 亿";
    if (v >= 1e4) return (v / 1e4).toFixed(2) + " 万";
    return v.toFixed(0);
  }

  function sparkSVG(values, color, w, h, cls) {
    if (!values || values.length < 2) {
      // 无历史数据时画一条淡淡的基准线，避免卡片下方出现空洞
      return `<svg class="${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <path d="M0 ${h / 2} H${w}" fill="none" stroke="${color}" stroke-opacity="0.18"
              stroke-width="1" stroke-dasharray="3 4" vector-effect="non-scaling-stroke"/>
      </svg>`;
    }
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = w / (values.length - 1);
    const pts = values.map((v, i) => [
      i * stepX,
      h - 2 - ((v - min) / span) * (h - 4),
    ]);
    const line = pts
      .map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1))
      .join(" ");
    const area = `${line} L ${w} ${h} L 0 ${h} Z`;
    return `<svg class="${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <path d="${area}" fill="${color}" fill-opacity="0.10"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="1.4"
            stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    </svg>`;
  }

  /* ─────────────────────────── 提示条 ─────────────────────────── */
  let toastTimer = null;
  function toast(msg, isErr = false) {
    const box = $("#toast");
    box.textContent = msg;
    box.classList.toggle("err", isErr);
    box.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("show"), 2600);
  }

  /* ─────────────────────────── 时钟 ───────────────────────────── */
  function tickClock() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    $("#clock-time").textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    const week = "日一二三四五六"[now.getDay()];
    $("#clock-date").textContent = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} 周${week}`;
  }

  /* ─────────────────────────── 市场状态 ───────────────────────── */
  function renderSessions(sessions) {
    const box = $("#sessions");
    const keys = Object.keys(sessions || {});
    if (box.children.length !== keys.length) {
      box.innerHTML = keys
        .map((k) => `<span class="session" data-key="${k}" data-state="closed"><i class="dot"></i><b>${k}</b><span class="lbl">—</span></span>`)
        .join("");
    }
    keys.forEach((k) => {
      const el = box.querySelector(`[data-key="${CSS.escape(k)}"]`);
      if (!el) return;
      el.dataset.state = sessions[k].state;
      el.querySelector(".lbl").textContent = sessions[k].label;
    });
  }

  /* ─────────────────────────── 跑马灯 ─────────────────────────── */
  function buildTicker(data) {
    const picks = [];
    data.groups.forEach((g) => {
      g.assets.forEach((a) => {
        if (picks.length < 18 && a.price != null) picks.push(a);
      });
    });
    const track = $("#ticker-track");
    const items = picks.map((a, i) => ({ a, i }));
    const html = items
      .map(
        ({ a }, i) => `<span class="tick" data-id="${a.id}" data-i="${i}">
          <b>${a.name}</b><span class="p">—</span><em class="c">—</em></span>`
      )
      .join("");
    track.innerHTML = html + html;
    state.tickerItems = track.querySelectorAll(".tick");
    state.tickerBuilt = true;
  }

  function updateTicker(assetsById) {
    state.tickerItems.forEach((el) => {
      const a = assetsById.get(el.dataset.id);
      if (!a) return;
      el.querySelector(".p").textContent = num(a.price, a.decimals);
      const c = el.querySelector(".c");
      c.textContent = pctText(a.pct);
      c.style.color = colorOf(a.pct);
    });
  }

  /* ─────────────────────────── 顶部大卡 ───────────────────────── */
  function buildHero(data) {
    const box = $("#hero");
    const map = new Map();
    data.groups.forEach((g) => g.assets.forEach((a) => map.set(a.id, a)));
    const ids = data.hero || [];
    box.innerHTML = ids
      .map((id) => {
        const a = map.get(id);
        if (!a) return "";
        return `<article class="hero-card" data-id="${id}" tabindex="0">
          <div class="hc-name">${a.name}<span class="hc-tag">${a.tag || ""}</span></div>
          <div class="hc-price"><span data-role="price">—</span><i class="unit" data-role="unit"></i></div>
          <div class="hc-delta"><span data-role="pct">—</span><small data-role="chg">—</small>
            <small class="hc-vol" data-role="vol"></small></div>
          <div class="hc-spark" data-role="spark"></div>
        </article>`;
      })
      .join("");
    state.heroRefs.clear();
    box.querySelectorAll(".hero-card").forEach((root) => {
      const ref = {
        root,
        price: root.querySelector('[data-role="price"]'),
        unit: root.querySelector('[data-role="unit"]'),
        pct: root.querySelector('[data-role="pct"]'),
        chg: root.querySelector('[data-role="chg"]'),
        vol: root.querySelector('[data-role="vol"]'),
        spark: root.querySelector('[data-role="spark"]'),
        lastSpark: "",
        lastPrice: null,
      };
      state.heroRefs.set(root.dataset.id, ref);
      root.addEventListener("click", () => selectAsset(root.dataset.id));
      root.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") selectAsset(root.dataset.id);
      });
    });
  }

  function updateHero(a) {
    const ref = state.heroRefs.get(a.id);
    if (!ref) return;
    ref.price.textContent = num(a.price, a.decimals);
    ref.unit.textContent = unitOf(a);
    ref.pct.textContent = deltaText(a);
    ref.pct.className = tone(dirOf(a));
    ref.chg.textContent = signed(a.change, a.decimals);
    ref.chg.style.color = colorOf(dirOf(a));
    ref.vol.textContent = a.volume ? `量 ${fmtVol(a.volume)}` : "";
    const key = a.spark.length + ":" + (a.spark[a.spark.length - 1] ?? "");
    if (key !== ref.lastSpark) {
      ref.lastSpark = key;
      ref.spark.innerHTML = sparkSVG(a.spark, colorOf(dirOf(a)), 200, 46, "spark");
    }
    ref.root.classList.toggle("active", a.id === state.selected);
  }

  /* ─────────────────────────── 分组卡片 ───────────────────────── */
  function buildGroups(data) {
    const box = $("#groups");
    box.innerHTML = data.groups
      .map(
        (g) => `<section class="group" data-group="${g.name}">
        <div class="group-head">
          <h2><span class="gicon">${g.icon || ""}</span>${g.name}</h2>
          <span class="count">${g.assets.length}</span>
          <span class="rule"></span>
        </div>
        <div class="grid">
          ${g.assets
            .map(
              (a) => `<article class="card" data-id="${a.id}" tabindex="0">
              <div class="card-top">
                <span class="card-name" title="${a.name}">${a.name}</span>
                <span class="card-tag">${a.tag || ""}</span>
              </div>
              <div class="card-mid">
                <span class="price-wrap"><span class="card-price" data-role="price">—</span><i class="unit" data-role="unit"></i></span>
                <span class="card-pct" data-role="pct">—</span>
              </div>
              <div class="card-spark" data-role="spark"></div>
              <div class="card-vol" data-role="vol"></div>
            </article>`
            )
            .join("")}
        </div>
      </section>`
      )
      .join("");

    state.cardRefs.clear();
    box.querySelectorAll(".card").forEach((root) => {
      const ref = {
        root,
        price: root.querySelector('[data-role="price"]'),
        unit: root.querySelector('[data-role="unit"]'),
        pct: root.querySelector('[data-role="pct"]'),
        spark: root.querySelector('[data-role="spark"]'),
        vol: root.querySelector('[data-role="vol"]'),
        lastPrice: null,
        lastSpark: "",
      };
      state.cardRefs.set(root.dataset.id, ref);
      root.addEventListener("click", () => selectAsset(root.dataset.id));
      root.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") selectAsset(root.dataset.id);
      });
    });
  }

  function updateCard(a) {
    const ref = state.cardRefs.get(a.id);
    if (!ref) return;
    const priceTxt = a.price == null ? "—" : num(a.price, a.decimals);
    if (ref.price.textContent !== priceTxt) {
      ref.price.textContent = priceTxt;
      if (ref.lastPrice != null && a.price != null && Math.abs(a.price - ref.lastPrice) > 1e-12) {
        ref.root.classList.remove("flash-up", "flash-down");
        void ref.root.offsetWidth;
        ref.root.classList.add(a.price > ref.lastPrice ? "flash-up" : "flash-down");
      }
    }
    if (a.price != null) ref.lastPrice = a.price;
    ref.unit.textContent = unitOf(a);

    ref.pct.textContent = deltaText(a);
    ref.pct.className = "card-pct " + tone(dirOf(a));
    ref.root.classList.toggle("active", a.id === state.selected);
    ref.root.classList.toggle("card-missing", a.price == null);

    const key = a.spark.length ? a.spark.length + ":" + a.spark[a.spark.length - 1] : "";
    if (key !== ref.lastSpark) {
      ref.lastSpark = key;
      ref.spark.innerHTML = sparkSVG(a.spark, colorOf(dirOf(a)), 180, 30, "spark");
    }
    ref.vol.textContent = a.volume ? `量 ${fmtVol(a.volume)}` : "";
  }

  /* ─────────────────────────── 详情面板 ───────────────────────── */
  function renderStats(a) {
    const rows = [
      ["最新价", withUnit(num(a.price, a.decimals), a)],
      ["涨跌额", withUnit(signed(a.change, a.decimals), a)],
      [a.kind === "spread" ? "变动" : "涨跌幅", deltaText(a)],
    ];
    if (a.open != null) rows.push(["今开", withUnit(num(a.open, a.decimals), a)]);
    if (a.high != null) rows.push(["最高", withUnit(num(a.high, a.decimals), a)]);
    if (a.low != null) rows.push(["最低", withUnit(num(a.low, a.decimals), a)]);
    if (a.prev_close != null) rows.push(["昨收", withUnit(num(a.prev_close, a.decimals), a)]);
    if (a.volume != null) rows.push(["成交量", fmtVol(a.volume)]);
    if (a.amount != null) rows.push(["成交额", fmtVol(a.amount)]);
    rows.push(["更新时间", a.ts || "—"]);
    rows.push(["数据源", (a.source || "—").toUpperCase()]);

    const stats = $("#stats");
    stats.innerHTML = rows
      .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
      .join("");
    const dd = stats.querySelectorAll("dd");
    if (dd[1]) dd[1].style.color = colorOf(dirOf(a));
    if (dd[2]) dd[2].style.color = colorOf(dirOf(a));
    $("#stats-tag").textContent = a.tag || "";

    $("#chart-name").textContent = a.name;
    $("#chart-sub").textContent = a.id;
    const priceBox = $("#chart-price");
    priceBox.querySelector("strong").textContent = num(a.price, a.decimals);
    $("#chart-unit").textContent = unitOf(a);
    const delta = priceBox.querySelector(".delta");
    delta.textContent = `${signed(a.change, a.decimals)}  ${deltaText(a)}`;
    delta.style.color = colorOf(dirOf(a));
  }

  /* 区间表现：由服务端按完整日线计算，不受图表窗口影响 */
  function renderPerf(payload) {
    const box = $("#perf");
    const rows = (payload && payload.perf) || [];
    if (!rows.length) {
      box.innerHTML = "";
      return;
    }
    const isYield = payload.kind === "bond" || payload.kind === "spread";
    box.innerHTML = rows
      .map(({ label, value }) => {
        if (value == null) {
          return `<div class="perf-row"><span>${label}</span><b class="flat">—</b></div>`;
        }
        const text = isYield
          ? `${value > 0 ? "+" : ""}${value.toFixed(0)} bp`
          : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
        return `<div class="perf-row"><span>${label}</span><b class="${tone(value)}">${text}</b></div>`;
      })
      .join("");
  }

  /* ─────────────────────────── 图表 ───────────────────────────── */
  function initChart() {
    if (typeof echarts === "undefined") {
      $("#chart").innerHTML =
        '<div style="padding:40px;color:#8b95a9;font-size:13px">图表库未加载：请确认 static/vendor/echarts.min.js 存在，或联网后刷新。</div>';
      return;
    }
    state.chart = echarts.init($("#chart"), null, { renderer: "canvas" });
    window.addEventListener("resize", () => {
      state.chart && state.chart.resize();
      mf.chart && mf.chart.resize();
      syncSecListHeight();
    });
    // 容器尺寸变化（窗口缩放、侧栏折叠）时保持图表自适应
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => state.chart && state.chart.resize()).observe($("#chart"));
    }
  }

  const AXIS_LABEL = { color: "#5d6679", fontSize: 10.5, fontFamily: "JetBrains Mono, Consolas, monospace" };
  const SPLIT_LINE = { lineStyle: { color: "rgba(255,255,255,0.05)", type: "dashed" } };

  function movingAverage(closes, n) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < closes.length; i++) {
      sum += closes[i];
      if (i >= n) sum -= closes[i - n];
      out.push(i >= n - 1 ? +(sum / n).toFixed(4) : null);
    }
    return out;
  }

  function renderChart(payload) {
    if (!state.chart) return;
    const bars = payload.bars || [];
    const d = payload.decimals ?? 2;
    const isLine = ["bond", "fx", "spread"].includes(payload.kind);

    if (!bars.length) {
      state.chart.clear();
      $("#chart-status").textContent = "暂无历史数据";
      $("#chart-source").textContent = payload.source ? `来源 ${payload.source}` : "";
      renderPerf(payload);
      $("#chart-hint").textContent =
        payload.source === "snapshot"
          ? "该指标暂无公开历史接口，面板每日记录收盘值，随运行逐步积累成曲线。"
          : "";
      return;
    }

    const dates = bars.map((b) => b[0]);
    const closes = bars.map((b) => b[4]);
    const candles = bars.map((b) => [b[1], b[4], b[3], b[2]]);
    const volumes = bars.map((b, i) => ({
      value: b[5],
      itemStyle: { color: b[4] >= b[1] ? "rgba(255,84,104,.55)" : "rgba(25,196,138,.5)" },
    }));

    const compact = state.range === "3Y" || state.range === "ALL";
    const fmtDate = (v) => (compact ? v.slice(0, 7) : v.slice(5));

    const series = [];
    if (isLine) {
      series.push({
        name: payload.name,
        type: "line",
        data: closes,
        showSymbol: false,
        lineStyle: { width: 1.8, color: "#7dd3fc" },
        itemStyle: { color: "#7dd3fc" },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(125,211,252,.28)" },
            { offset: 1, color: "rgba(125,211,252,0)" },
          ]),
        },
      });
    } else {
      series.push({
        name: payload.name,
        type: "candlestick",
        data: candles,
        itemStyle: {
          color: UP,
          color0: DOWN,
          borderColor: UP,
          borderColor0: DOWN,
        },
      });
      const ma5 = movingAverage(closes, 5);
      const ma20 = movingAverage(closes, 20);
      const ma60 = movingAverage(closes, 60);
      const maSeries = [
        ["MA5", ma5, "#f6c76b"],
        ["MA20", ma20, "#7dd3fc"],
        ["MA60", ma60, "#b499ff"],
      ];
      maSeries.forEach(([name, data, color]) => {
        series.push({
          name,
          type: "line",
          data,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1, color, opacity: 0.85 },
          itemStyle: { color },
          emphasis: { disabled: true },
        });
      });
    }

    const hasVolume = bars.some((b) => b[5] != null && b[5] > 0);
    if (hasVolume) {
      series.push({
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
        barWidth: "60%",
      });
    }

    const option = {
      animation: false,
      backgroundColor: "transparent",
      title: hasVolume
        ? [{
            text: "成交量",
            left: 10,
            top: "70%",
            textStyle: { color: "#5d6679", fontSize: 10, fontWeight: "normal" },
          }]
        : [],
      grid: hasVolume
        ? [
            { left: 12, right: 58, top: 16, height: "62%" },
            { left: 12, right: 58, top: "74%", height: "13%" },
          ]
        : [{ left: 12, right: 58, top: 16, bottom: 44 }],
      axisPointer: {
        link: [{ xAxisIndex: "all" }],
        label: { backgroundColor: "#1b2333", color: "#dfe6f3", fontSize: 10 },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", crossStyle: { color: "rgba(255,255,255,.2)" } },
        backgroundColor: "rgba(12,17,27,.96)",
        borderColor: "rgba(255,255,255,.09)",
        borderWidth: 1,
        padding: [8, 11],
        textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
        formatter: (params) => {
          const p = params.find((x) => x.seriesType === "candlestick" || x.seriesType === "line");
          if (!p) return "";
          const i = p.dataIndex;
          const b = bars[i];
          if (p.seriesType === "candlestick") {
            const chg = ((b[4] - b[1]) / (b[1] || 1)) * 100;
            const vol = b[5] == null ? "" : `<br/>量 ${fmtVol(b[5])}`;
            return `<div style="color:#8b95a9;margin-bottom:4px">${b[0]}</div>
              开 ${num(b[1], d)}　收 ${num(b[4], d)}<br/>
              高 ${num(b[2], d)}　低 ${num(b[3], d)}<br/>
              <span style="color:${chg >= 0 ? UP : DOWN}">涨跌 ${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span>${vol}`;
          }
          return `<div style="color:#8b95a9;margin-bottom:4px">${b[0]}</div>${num(b[4], d)}${payload.unit || ""}`;
        },
      },
      xAxis: [
        {
          type: "category",
          data: dates,
          boundaryGap: !isLine,
          axisLine: { lineStyle: { color: "rgba(255,255,255,.10)" } },
          axisTick: { show: false },
          axisLabel: { ...AXIS_LABEL, formatter: fmtDate, hideOverlap: true },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          position: "right",
          splitNumber: 5,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: {
            ...AXIS_LABEL,
            formatter: (v) => Number(v).toFixed(d),
          },
          splitLine: SPLIT_LINE,
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: hasVolume ? [0, 1] : [0], start: 0, end: 100 },
        {
          type: "slider",
          xAxisIndex: hasVolume ? [0, 1] : [0],
          height: 14,
          bottom: 4,
          borderColor: "transparent",
          backgroundColor: "rgba(255,255,255,.03)",
          fillerColor: "rgba(94,234,212,.10)",
          handleStyle: { color: "rgba(94,234,212,.5)" },
          moveHandleStyle: { color: "rgba(94,234,212,.35)" },
          textStyle: { color: "#5d6679", fontSize: 9 },
          dataBackground: {
            lineStyle: { color: "rgba(255,255,255,.12)" },
            areaStyle: { color: "rgba(255,255,255,.05)" },
          },
          selectedDataBackground: {
            lineStyle: { color: "rgba(94,234,212,.4)" },
            areaStyle: { color: "rgba(94,234,212,.08)" },
          },
        },
      ],
      series,
    };

    if (hasVolume) {
      option.xAxis.push({
        type: "category",
        gridIndex: 1,
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: "rgba(255,255,255,.08)" } },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      });
      option.yAxis.push({
        gridIndex: 1,
        scale: true,
        position: "right",
        splitNumber: 2,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { ...AXIS_LABEL, formatter: (v) => (v >= 1e8 ? (v / 1e8).toFixed(1) + "亿" : v >= 1e4 ? (v / 1e4).toFixed(0) + "万" : v) },
        splitLine: { show: false },
      });
    }

    state.chart.setOption(option, true);
    renderPerf(payload);
    const hint = $("#chart-hint");
    if (payload.source === "snapshot") {
      hint.textContent = "该指标暂无公开历史接口，面板每日记录收盘值，随运行逐步积累成曲线。";
    } else if (payload.kind === "spread") {
      hint.textContent = "利差 = 美债 10Y − 美债 2Y；正值表示长端利率高于短端。";
    } else {
      hint.textContent = "";
    }
    $("#chart-status").textContent = `${bars.length} 根 · ${bars[0][0]} → ${bars[bars.length - 1][0]}`;
    $("#chart-source").textContent = payload.source ? `来源 ${payload.source}` : "";
  }

  async function loadChart(force = false) {
    if (!state.selected) return;
    const key = `${state.selected}|${state.mode}|${state.range}`;
    if (!force && key === state.chartKey) return;
    state.chartKey = key;
    $("#chart-status").textContent = "加载中…";
    try {
      if (state.mode === "intraday") {
        const res = await fetch(`/api/intraday?id=${encodeURIComponent(state.selected)}`);
        if (!res.ok) throw new Error("HTTP " + res.status);
        const payload = await res.json();
        state.intradayAt = Date.now();
        renderIntraday(payload);
      } else if (state.mode === "flow") {
        const res = await fetch(`/api/flow?id=${encodeURIComponent(state.selected)}&limit=120`);
        if (!res.ok) throw new Error("HTTP " + res.status);
        renderFlow(await res.json());
      } else {
        const res = await fetch(`/api/history?id=${encodeURIComponent(state.selected)}&range=${state.range}`);
        if (!res.ok) throw new Error("HTTP " + res.status);
        renderChart(await res.json());
      }
    } catch (err) {
      $("#chart-status").textContent = "数据加载失败：" + err.message;
    }
  }

  /* 资金流：流入 / 流出 / 净流入占比（%） */
  const pctPoint = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);

  function renderFlow(payload) {
    if (!state.chart) return;
    const rows = payload.rows || [];
    if (!rows.length) {
      state.chart.clear();
      $("#chart-status").textContent = `${payload.name || ""} 暂无资金流数据（该标的没有成交量）`;
      $("#chart-source").textContent = "";
      return;
    }
    const dates = rows.map((r) => r[0]);
    const inflow = rows.map((r) => r[1]);
    const net = rows.map((r) => r[3]);
    let acc = 0;
    const cum = net.map((v) => (acc += v));
    const ma = net.map((_, i) => {
      const part = net.slice(Math.max(0, i - 4), i + 1);
      return part.reduce((a, b) => a + b, 0) / part.length;
    });
    const s = payload.summary || {};

    state.chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { left: 46, right: 58, top: 30, bottom: 34 },
        legend: {
          top: 0, left: 0, itemWidth: 14, itemHeight: 8, itemGap: 14,
          textStyle: { color: "#8b95a9", fontSize: 10.5 }, inactiveColor: "#3c4557",
        },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross", crossStyle: { color: "rgba(255,255,255,.2)" } },
          backgroundColor: "rgba(12,17,27,.96)",
          borderColor: "rgba(255,255,255,.09)",
          borderWidth: 1,
          padding: [8, 11],
          textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
          formatter: (params) => {
            if (!params.length) return "";
            const i = params[0].dataIndex;
            return `<div style="color:#8b95a9;margin-bottom:4px">${dates[i]}</div>
              流入 ${inflow[i].toFixed(1)}%　流出 ${(100 - inflow[i]).toFixed(1)}%<br/>
              <span style="color:${net[i] >= 0 ? UP : DOWN}">净流入 ${pctPoint(net[i])}</span><br/>
              累计 ${pctPoint(cum[i])}　5日均 ${pctPoint(ma[i])}`;
          },
        },
        xAxis: {
          type: "category", data: dates, boundaryGap: true,
          axisLine: { lineStyle: { color: "rgba(255,255,255,.10)" } },
          axisTick: { show: false },
          axisLabel: { ...AXIS_LABEL, formatter: (v) => v.slice(5), hideOverlap: true },
          splitLine: { show: false },
        },
        yAxis: [
          {
            type: "value", min: -100, max: 100, interval: 50, position: "right",
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { ...AXIS_LABEL, formatter: (v) => v + "%" },
            splitLine: SPLIT_LINE,
          },
          {
            type: "value", position: "left", scale: true,
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { ...AXIS_LABEL, formatter: (v) => v.toFixed(0) },
            splitLine: { show: false },
            name: "累计", nameTextStyle: { color: "#5d6679", fontSize: 9, padding: [0, 0, 0, 6] },
          },
        ],
        series: [
          {
            name: "每日净流入（%）",
            type: "bar",
            data: net.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? "rgba(255,84,104,.75)" : "rgba(25,196,138,.75)" } })),
            barMaxWidth: 9,
            z: 2,
          },
          {
            name: "5日均净流入（%）",
            type: "line", data: ma, showSymbol: false, z: 4,
            lineStyle: { width: 1.6, color: "#e8edf7" }, itemStyle: { color: "#e8edf7" },
          },
          {
            name: "累计净流入（%）",
            type: "line", data: cum, yAxisIndex: 1, showSymbol: false, z: 5,
            lineStyle: { width: 2.0, color: "#5eead4" }, itemStyle: { color: "#5eead4" },
          },
        ],
      },
      true
    );

    $("#chart-status").textContent =
      `${payload.name} · 最近净流入 ${pctPoint(s.latest)} · 区间累计 ${pctPoint(s.cumulative)}`;
    $("#chart-source").textContent = "资金流（按成交额位置估算）";
  }

  /* 市场资金流：各市场净流入占比曲线 */
  const mf = { chart: null, data: null, at: 0 };
  const MF_COLORS = {
    "A股": "#ff5468", "港股": "#f6c76b", "美股": "#7dd3fc",
    "ETF(A股)": "#a78bfa", "ETF(美股)": "#c4b5fd", "ETF(港股)": "#fbbf24",
    "期货": "#5eead4", "债券": "#f9a8d4", "利率": "#94a3b8",
  };

  async function fetchMarketFlow() {
    try {
      const res = await fetch("/api/flow/markets", { cache: "no-store" });
      const data = await res.json();
      mf.data = data;
      mf.at = Date.now();
      renderMarketFlow(data);
    } catch (err) {
      $("#mf-status").textContent = "市场资金流加载失败";
    }
  }

  function renderMarketFlow(data) {
    const groups = (data.groups || []).filter((g) => g.rows && g.rows.length);
    if (!groups.length) {
      $("#mf-status").textContent = "暂无数据";
      return;
    }
    if (!mf.chart) mf.chart = echarts.init($("#mf-chart"));
    const dates = [...new Set(groups.flatMap((g) => g.rows.map((r) => r[0])))].sort();
    const series = groups.map((g) => {
      const map = new Map(g.rows.map((r) => [r[0], r[1]]));
      const color = MF_COLORS[g.group] || "#8b95a9";
      let acc = 0;
      const data = dates.map((d) => {
        if (!map.has(d)) return null;
        acc += map.get(d);
        return Math.round(acc * 100) / 100;
      });
      return {
        name: g.group,
        type: "line",
        data,
        showSymbol: false,
        connectNulls: true,
        lineStyle: { width: 1.7, color },
        itemStyle: { color },
      };
    });

    mf.chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { left: 46, right: 56, top: 28, bottom: 26 },
        legend: {
          top: 0, left: 0, itemWidth: 14, itemHeight: 3, itemGap: 14,
          textStyle: { color: "#8b95a9", fontSize: 10.5 }, inactiveColor: "#3c4557",
        },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross", crossStyle: { color: "rgba(255,255,255,.2)" } },
          backgroundColor: "rgba(12,17,27,.96)",
          borderColor: "rgba(255,255,255,.09)",
          borderWidth: 1,
          padding: [8, 11],
          textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
        },
        xAxis: {
          type: "category", data: dates, boundaryGap: false,
          axisLine: { lineStyle: { color: "rgba(255,255,255,.10)" } },
          axisTick: { show: false },
          axisLabel: { ...AXIS_LABEL, formatter: (v) => v.slice(5), hideOverlap: true },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value", scale: true, position: "right",
          axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { ...AXIS_LABEL, formatter: (v) => v.toFixed(0) },
          splitLine: SPLIT_LINE,
          name: "累计占比", nameTextStyle: { color: "#5d6679", fontSize: 9 },
        },
        dataZoom: [{ type: "inside", start: 0, end: 100 }],
        series,
      },
      true
    );
    $("#mf-status").textContent = `截至 ${data.date || "—"} · 累计净流入占比`;
  }

  /* 当日分时：价格折线 + 昨收基准线 + 分钟成交量 */
  function renderIntraday(payload) {
    if (!state.chart) return;
    const pts = payload.points || [];
    if (!pts.length) {
      state.chart.clear();
      $("#chart-status").textContent = `${payload.name || ""} 暂无分时数据（债券等标的没有盘中行情）`;
      $("#chart-source").textContent = "";
      return;
    }
    const times = pts.map((p) => p[0]);
    const prices = pts.map((p) => p[1]);
    const vols = pts.map((p) => (p[2] == null ? null : p[2]));
    const d = payload.decimals ?? 2;
    const prev = payload.prev_close;
    const last = prices[prices.length - 1];
    const up = prev == null ? true : last >= prev;
    const color = up ? UP : DOWN;
    const hasVol = vols.some((v) => v != null && v > 0);
    const lo = Math.min(...prices, ...(prev != null ? [prev] : []));
    const hi = Math.max(...prices, ...(prev != null ? [prev] : []));
    const pad = (hi - lo) * 0.08 || Math.abs(hi * 0.002) || 1;

    const series = [
      {
        name: payload.name,
        type: "line",
        data: prices,
        showSymbol: false,
        lineStyle: { width: 1.8, color },
        itemStyle: { color },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: up ? "rgba(255,84,104,.28)" : "rgba(25,196,138,.28)" },
            { offset: 1, color: "rgba(0,0,0,0)" },
          ]),
        },
        markLine: prev == null ? undefined : {
          silent: true,
          symbol: "none",
          label: {
            show: true,
            position: "insideEndTop",
            formatter: `昨收 ${num(prev, d)}`,
            color: "#5d6679",
            fontSize: 10,
          },
          lineStyle: { color: "rgba(255,255,255,.22)", type: "dashed" },
          data: [{ yAxis: prev }],
        },
      },
    ];
    if (hasVol) {
      series.push({
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: vols,
        barWidth: "55%",
        itemStyle: { color: up ? "rgba(255,84,104,.45)" : "rgba(25,196,138,.45)" },
      });
    }

    const option = {
      animation: false,
      backgroundColor: "transparent",
      title: hasVol
        ? [{
            text: "成交量",
            left: 10,
            top: "72%",
            textStyle: { color: "#5d6679", fontSize: 10, fontWeight: "normal" },
          }]
        : [],
      grid: hasVol
        ? [{ left: 12, right: 58, top: 20, height: "62%" }, { left: 12, right: 58, top: "76%", height: "13%" }]
        : [{ left: 12, right: 58, top: 20, bottom: 36 }],
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", crossStyle: { color: "rgba(255,255,255,.2)" } },
        backgroundColor: "rgba(12,17,27,.96)",
        borderColor: "rgba(255,255,255,.09)",
        borderWidth: 1,
        padding: [8, 11],
        textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
        formatter: (params) => {
          const p = params[0];
          if (!p) return "";
          const i = p.dataIndex;
          const price = prices[i];
          const pct = prev ? (price / prev - 1) * 100 : null;
          const tone = pct == null ? "" : `<span style="color:${pct >= 0 ? UP : DOWN}"> ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%</span>`;
          const v = vols[i];
          const volLine = v == null ? "" : `<br/>量 ${fmtVol(v)}`;
          return `<div style="color:#8b95a9;margin-bottom:4px">${times[i]}</div>${num(price, d)}${tone}${volLine}`;
        },
      },
      xAxis: [
        {
          type: "category",
          data: times,
          boundaryGap: false,
          axisLine: { lineStyle: { color: "rgba(255,255,255,.10)" } },
          axisTick: { show: false },
          axisLabel: { ...AXIS_LABEL, interval: Math.max(1, Math.floor(times.length / 8)), hideOverlap: true },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          type: "value",
          position: "right",
          min: +(lo - pad).toFixed(4),
          max: +(hi + pad).toFixed(4),
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { ...AXIS_LABEL, formatter: (v) => Number(v).toFixed(d) },
          splitLine: SPLIT_LINE,
        },
      ],
      dataZoom: [{ type: "inside", start: 0, end: 100 }],
      series,
    };

    if (hasVol) {
      option.xAxis.push({
        type: "category",
        gridIndex: 1,
        data: times,
        boundaryGap: true,
        axisLine: { lineStyle: { color: "rgba(255,255,255,.08)" } },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      });
      option.yAxis.push({
        gridIndex: 1,
        scale: true,
        position: "right",
        splitNumber: 2,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          ...AXIS_LABEL,
          formatter: (v) => (v >= 1e8 ? (v / 1e8).toFixed(1) + "亿" : v >= 1e4 ? (v / 1e4).toFixed(0) + "万" : v),
        },
        splitLine: { show: false },
      });
    }

    state.chart.setOption(option, true);

    const tzNote = (payload.id || "").startsWith("us.") ? "（美东时间）" : "";
    $("#chart-status").textContent =
      `${payload.date || ""} · ${times[0]}–${times[times.length - 1]} · ${pts.length} 个点${tzNote}`;
    $("#chart-source").textContent = "分时";
  }

  /* ─────────────────────────── 数据获取 ───────────────────────── */
  function assetsById(data) {
    const map = new Map();
    data.groups.forEach((g) => g.assets.forEach((a) => map.set(a.id, a)));
    return map;
  }

  function renderAll(data) {
    const map = assetsById(data);

    if (!state.heroRefs.size) buildHero(data);
    if (!state.cardRefs.size) buildGroups(data);
    if (!state.tickerBuilt) buildTicker(data);

    renderSessions(data.sessions);
    map.forEach((a) => {
      updateHero(a);
      updateCard(a);
    });
    updateTicker(map);

    const sel = map.get(state.selected);
    if (sel) renderStats(sel);

    // 分时/资金流视图的说明文字由各自渲染函数维护，轮询只更新「最新快照时间」
    $("#chart-status").textContent =
      state.mode !== "kline"
        ? $("#chart-status").textContent
        : data.updated_at
          ? `快照更新 ${data.updated_at.replace("T", " ")}`
          : "等待数据…";
    if (!$("#view-analysis").hidden) initAnalysis();
    if (data.errors && data.errors.length && Date.now() - state.lastError > 30000) {
      state.lastError = Date.now();
      const e = data.errors[data.errors.length - 1];
      toast(`数据源告警：${e.scope} - ${e.error}`, true);
    }
  }

  async function fetchOverview() {
    if (state.building) return;
    state.building = true;
    try {
      const res = await fetch("/api/overview", { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      state.data = data;
      renderAll(data);
      // 分时视图每 30 秒刷新一次
      if (state.mode === "intraday" && !document.hidden && Date.now() - state.intradayAt > 30000) {
        state.chartKey = "";
        loadChart(true);
      }
      // 市场资金流每 5 分钟刷新一次
      if (Date.now() - mf.at > 300000) fetchMarketFlow();
    } catch (err) {
      toast("无法连接本地服务：" + err.message, true);
    } finally {
      state.building = false;
      schedule();
    }
  }

  function schedule() {
    clearTimeout(state.timer);
    if (!state.intervalMs) return;
    state.timer = setTimeout(() => {
      if (!document.hidden) fetchOverview();
      else schedule();
    }, state.intervalMs);
  }

  /* ─────────────────────────── 交互 ───────────────────────────── */
  function selectAsset(id, opts = {}) {
    if (!id) return;
    state.selected = id;
    state.chartKey = "";
    if (location.hash.slice(1) !== id) history.replaceState(null, "", "#" + encodeURIComponent(id));
    state.cardRefs.forEach((ref) => ref.root.classList.toggle("active", ref.root.dataset.id === id));
    state.heroRefs.forEach((ref) => ref.root.classList.toggle("active", ref.root.dataset.id === id));
    if (state.data) {
      const a = assetsById(state.data).get(id);
      if (a) renderStats(a);
    }
    loadChart(true);
    if (opts.scroll !== false) {
      $(".workbench").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function applySearch(q) {
    const term = q.trim().toLowerCase();
    const test = (root, id, name) =>
      !term || id.toLowerCase().includes(term) || name.toLowerCase().includes(term);
    state.cardRefs.forEach((ref, id) => {
      const name = ref.root.querySelector(".card-name").textContent;
      ref.root.classList.toggle("dimmed", !test(ref.root, id, name));
    });
    state.heroRefs.forEach((ref, id) => {
      const name = ref.root.querySelector(".hc-name").textContent;
      ref.root.classList.toggle("dimmed", !test(ref.root, id, name));
    });
  }

  async function manualRefresh() {
    const btn = $("#btn-refresh");
    btn.classList.add("busy");
    try {
      await fetch("/api/refresh", { method: "POST" });
      state.chartKey = "";
      await fetchOverview();
      await loadChart(true);
      toast("已刷新");
    } catch (err) {
      toast("刷新失败：" + err.message, true);
    } finally {
      btn.classList.remove("busy");
    }
  }

  async function startDownload() {
    const label = $("#download-label");
    try {
      const res = await fetch("/api/download", { method: "POST" });
      const data = await res.json();
      if (!data.started) {
        toast("下载任务已在进行中");
        return;
      }
      toast("开始下载历史数据…");
      clearInterval(state.downloadTimer);
      state.downloadTimer = setInterval(async () => {
        try {
          const s = await (await fetch("/api/download/status")).json();
          label.textContent = `${s.done}/${s.total}`;
          if (!s.running) {
            clearInterval(state.downloadTimer);
            label.textContent = "下载历史";
            const bad = (s.errors || []).length;
            toast(bad ? `下载完成，${bad} 个标的失败` : "历史数据下载完成", !!bad);
            state.chartKey = "";
            await loadChart(true);
          }
        } catch (_) {
          clearInterval(state.downloadTimer);
          label.textContent = "下载历史";
        }
      }, 900);
    } catch (err) {
      toast("启动下载失败：" + err.message, true);
    }
  }

  /* ─────────────────────────── 关联分析 ───────────────────────── */
  const AN_COLORS = {
    composite: "#e8edf7",
    pearson: "#7dd3fc",
    spearman: "#a78bfa",
    kendall: "#f6c76b",
    downside: "#ff8a94",
    volatility: "#5eead4",
    leadlag: "#9aa7bd",
  };

  const AN_METRIC_META = [
    { key: "pearson", short: "Pearson", color: "#7dd3fc" },
    { key: "spearman", short: "Spearman", color: "#a78bfa" },
    { key: "kendall", short: "Kendall", color: "#f6c76b" },
    { key: "downside", short: "下行", color: "#ff8a94" },
    { key: "volatility", short: "波动", color: "#5eead4" },
    { key: "leadlag", short: "滞后", color: "#9aa7bd" },
  ];

  const AN_DEFAULT_WEIGHTS = {
    pearson: 0.3, spearman: 0.2, kendall: 0.15,
    downside: 0.15, volatility: 0.1, leadlag: 0.1,
  };
  const AN_WEIGHT_KEY = "mm.analysis.weights";

  function loadAnWeights() {
    try {
      const raw = JSON.parse(localStorage.getItem(AN_WEIGHT_KEY) || "null");
      if (raw && AN_METRIC_META.every((m) => typeof raw[m.key] === "number")) {
        const out = {};
        AN_METRIC_META.forEach((m) => {
          out[m.key] = Math.max(0, Math.min(1, raw[m.key]));
        });
        if (Object.values(out).some((v) => v > 0)) return out;
      }
    } catch (_) {
      /* 忽略本地缓存异常 */
    }
    return { ...AN_DEFAULT_WEIGHTS };
  }

  const an = {
    ids: [],
    window: 60,
    range: "2Y",
    weights: loadAnWeights(),
    data: null,
    chart: null,
    scatter: null,
    observer: null,
    ready: false,
    timer: null,
  };

  function anWeightTotal() {
    return AN_METRIC_META.reduce((sum, m) => sum + (an.weights[m.key] || 0), 0);
  }

  function anWeightPct(key) {
    const total = anWeightTotal();
    return total > 0 ? Math.round(((an.weights[key] || 0) / total) * 100) : 0;
  }

  function updateWeightHint() {
    const hint = $("#weight-hint");
    if (!hint) return;
    hint.textContent =
      anWeightTotal() <= 0
        ? "权重全为 0，将按等权计算"
        : AN_METRIC_META.map((m) => `${m.short} ${anWeightPct(m.key)}%`).join(" · ");
    document.querySelectorAll(".wp-row").forEach((row) => {
      const badge = row.querySelector("b");
      if (badge) badge.textContent = anWeightPct(row.dataset.key) + "%";
    });
  }

  function buildWeightPanel() {
    const box = $("#weight-panel");
    if (!box) return;
    box.innerHTML = AN_METRIC_META.map(
      (m) => `<label class="wp-row" data-key="${m.key}">
        <span style="color:${m.color}">${m.short}</span>
        <input type="range" min="0" max="100" step="5" value="${Math.round((an.weights[m.key] || 0) * 100)}">
        <b>0%</b>
      </label>`
    ).join("");
    updateWeightHint();
  }

  function saveAnWeights() {
    try {
      localStorage.setItem(AN_WEIGHT_KEY, JSON.stringify(an.weights));
    } catch (_) {
      /* 忽略 */
    }
  }

  function anName(id) {
    if (!state.data) return id;
    for (const g of state.data.groups) {
      const hit = g.assets.find((a) => a.id === id);
      if (hit) return hit.name;
    }
    return id;
  }

  function anQual(v) {
    if (v == null) return "无数据";
    const a = Math.abs(v);
    const dir = v > 0 ? "正" : "负";
    if (a >= 0.8) return `强${dir}相关`;
    if (a >= 0.5) return `中等${dir}相关`;
    if (a >= 0.3) return `弱${dir}相关`;
    return "基本不相关";
  }

  const anVal = (v) => (v == null ? "—" : v.toFixed(3));

  /* 计价单位：利率用 %，其余按标的货币显示 RMB / 美元 / 港元 */
  function unitOf(a) {
    if (!a) return "";
    if (a.unit) return a.unit;
    if (a.currency === "CNY") return "RMB";
    if (a.currency === "USD") return "美元";
    if (a.currency === "HKD") return "港元";
    return "";
  }

  function withUnit(text, a) {
    const u = unitOf(a);
    return u ? `${text}<i class="u">${u}</i>` : text;
  }

  function buildPickList(filter = "") {
    const box = $("#asset-list");
    if (!state.data) return;
    const term = filter.trim().toLowerCase();
    let html = "";
    state.data.groups.forEach((g) => {
      const items = g.assets.filter(
        (a) => !term || a.name.toLowerCase().includes(term) || a.id.toLowerCase().includes(term)
      );
      if (!items.length) return;
      html += `<div class="pick-group">${g.icon || ""} ${g.name}</div>`;
      html += items
        .map(
          (a) => `<label class="pick-row ${an.ids.includes(a.id) ? "on" : ""}" data-id="${a.id}">
            <input type="checkbox" ${an.ids.includes(a.id) ? "checked" : ""}>
            <span class="pr-name">${a.name}</span><span class="pr-tag">${a.tag || ""}</span>
          </label>`
        )
        .join("");
    });
    box.innerHTML = html || `<div class="pick-group">没有匹配的标的</div>`;
  }

  function syncPicks() {
    $("#picked-count").textContent = `已选 ${an.ids.length}`;
    $("#picked-chips").innerHTML = an.ids
      .map(
        (id) => `<span class="pchip">${anName(id)}<button data-remove="${id}" title="移除">×</button></span>`
      )
      .join("");
    document.querySelectorAll(".pick-row").forEach((row) => {
      const on = an.ids.includes(row.dataset.id);
      row.classList.toggle("on", on);
      const box = row.querySelector("input");
      if (box) box.checked = on;
    });
  }

  function toggleAsset(id) {
    const idx = an.ids.indexOf(id);
    if (idx >= 0) {
      an.ids.splice(idx, 1);
    } else {
      if (an.ids.length >= 8) {
        toast("最多同时分析 8 个标的");
        return;
      }
      an.ids.push(id);
    }
    syncPicks();
    scheduleAnalysis();
  }

  function scheduleAnalysis() {
    clearTimeout(an.timer);
    if (an.ids.length < 2) {
      runAnalysis();
      return;
    }
    an.timer = setTimeout(runAnalysis, 260);
  }

  function initAnalysis() {
    if (an.ready || !state.data) return;
    an.ready = true;
    try {
      const qIds = new URLSearchParams(location.search).get("ids");
      if (qIds) {
        const valid = new Set();
        state.data.groups.forEach((g) => g.assets.forEach((a) => valid.add(a.id)));
        an.ids = qIds.split(",").map((s) => s.trim()).filter((id) => valid.has(id)).slice(0, 8);
      }
    } catch (_) {
      /* 忽略 */
    }
    if (an.ids.length < 2) {
      an.ids = ["sh000001", "us.INX"].filter((id) => state.data.groups.some((g) => g.assets.some((a) => a.id === id)));
      if (an.ids.length < 2) an.ids = ["sh000001", "sz399001"];
    }
    buildPickList("");
    syncPicks();
    buildWeightPanel();
    try {
      if (new URLSearchParams(location.search).get("wp") === "1") {
        $("#weight-panel").hidden = false;
        $("#btn-weights").setAttribute("aria-expanded", "true");
        $("#btn-weights").classList.add("active");
      }
    } catch (_) {
      /* 忽略 */
    }
    if (!$("#view-analysis").hidden) scheduleAnalysis();
  }

  let anSeq = 0;

  async function runAnalysis() {
    const chart = $("#an-chart");
    if (an.ids.length < 2) {
      $("#an-status").textContent = "至少选择两个标的";
      $("#an-sub").textContent = "未选择";
      $("#an-summary").innerHTML = "";
      $("#an-pairs-wrap").hidden = true;
      $("#an-scatter-wrap").hidden = true;
      if (an.chart) an.chart.clear();
      return;
    }
    $("#an-status").textContent = "计算中…";
    const seq = ++anSeq;
    try {
      const w = AN_METRIC_META.map((m) => (an.weights[m.key] ?? 0).toFixed(2)).join(",");
      const url = `/api/analysis?ids=${encodeURIComponent(an.ids.join(","))}&window=${an.window}&range=${an.range}&weights=${w}`;
      const res = await fetch(url, { cache: "no-store" });
      const data = await res.json();
      if (seq !== anSeq) return; // 丢弃过期响应（拖动权重时）
      if (data.error) {
        $("#an-status").textContent = data.error;
        $("#an-summary").innerHTML = "";
        if (an.chart) an.chart.clear();
        return;
      }
      an.data = data;
      renderAnalysis(data);
    } catch (err) {
      $("#an-status").textContent = "计算失败：" + err.message;
    }
  }

  function renderAnalysis(d) {
    $("#an-title").textContent = "关联分析";
    const names = d.ids.map((id) => d.names[id] || id);
    $("#an-sub").textContent = names.length > 3 ? `${names.slice(0, 3).join(" · ")} 等 ${names.length} 个` : names.join(" × ");
    $("#an-status").textContent = `${d.window} 日滚动窗口 · 共同交易日 ${d.n_common}`;
    $("#an-period").textContent = `${d.period[0]} → ${d.period[1]}`;
    renderAnalysisChart(d);
    renderSummary(d);
    renderHelp(d.metrics);
    renderPairs(d);
    renderScatter(d);
  }

  function renderHelp(metrics) {
    $("#an-help-list").innerHTML = (metrics || [])
      .map((m) => {
        const color = AN_COLORS[m.key] || "#8b95a9";
        return `<li><span class="dot" style="background:${color}"></span><b>${m.label}</b>${m.desc}</li>`;
      })
      .join("");
  }

  function renderAnalysisChart(d) {
    if (!an.chart) an.chart = echarts.init($("#an-chart"));
    const fmtDate = (v) => v.slice(5);
    const series = d.metrics.map((m, i) => {
      const color = AN_COLORS[m.key] || "#8b95a9";
      const composite = !!m.composite;
      const s = {
        name: m.label,
        type: "line",
        data: m.values,
        showSymbol: false,
        connectNulls: false,
        z: composite ? 10 : 2,
        lineStyle: { width: composite ? 2.6 : 1.7, color },
        itemStyle: { color },
        emphasis: { focus: "series" },
      };
      if (i === 0) {
        s.markLine = {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: "rgba(255,255,255,.18)", type: "dashed" },
          data: [{ yAxis: 0 }],
        };
      }
      return s;
    });

    an.chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { left: 40, right: 18, top: 36, bottom: 44 },
        legend: {
          top: 0,
          left: 0,
          itemWidth: 14,
          itemHeight: 3,
          itemGap: 12,
          textStyle: { color: "#8b95a9", fontSize: 10.5 },
          inactiveColor: "#3c4557",
        },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross", crossStyle: { color: "rgba(255,255,255,.2)" } },
          backgroundColor: "rgba(12,17,27,.96)",
          borderColor: "rgba(255,255,255,.09)",
          borderWidth: 1,
          padding: [8, 11],
          textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
          formatter: (params) => {
            if (!params.length) return "";
            const rows = [...params]
              .filter((p) => p.value != null)
              .sort((a, b) => b.value - a.value)
              .map((p) => `<div style="display:flex;justify-content:space-between;gap:14px">
                <span style="color:${p.color}">${p.seriesName}</span><b>${p.value.toFixed(3)}</b></div>`)
              .join("");
            return `<div style="color:#8b95a9;margin-bottom:5px">${params[0].axisValue}</div>${rows || "—"}`;
          },
        },
        xAxis: {
          type: "category",
          data: d.dates,
          boundaryGap: false,
          axisLine: { lineStyle: { color: "rgba(255,255,255,.10)" } },
          axisTick: { show: false },
          axisLabel: { ...AXIS_LABEL, formatter: fmtDate, hideOverlap: true },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          min: -1,
          max: 1,
          interval: 0.5,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { ...AXIS_LABEL, formatter: (v) => Number(v).toFixed(1) },
          splitLine: SPLIT_LINE,
        },
        dataZoom: [
          { type: "inside", start: 0, end: 100 },
          {
            type: "slider",
            height: 12,
            bottom: 4,
            borderColor: "transparent",
            backgroundColor: "rgba(255,255,255,.03)",
            fillerColor: "rgba(94,234,212,.10)",
            handleStyle: { color: "rgba(94,234,212,.5)" },
            moveHandleStyle: { color: "rgba(94,234,212,.35)" },
            textStyle: { color: "#5d6679", fontSize: 9 },
            dataBackground: { lineStyle: { color: "rgba(255,255,255,.12)" }, areaStyle: { color: "rgba(255,255,255,.05)" } },
          },
        ],
        series,
      },
      true
    );
  }

  function renderSummary(d) {
    $("#an-summary").innerHTML = d.summary
      .map((s) => {
        const v = s.latest;
        const color = v == null ? "#8b95a9" : v > 0 ? "var(--up)" : "var(--down)";
        const pos = v == null ? 50 : ((v + 1) / 2) * 100;
        return `<div class="an-card ${s.composite ? "primary" : ""}">
          <span class="ac-name">${s.label}</span>
          <b class="ac-val" style="color:${color}">${anVal(v)}</b>
          <div class="ac-track"><i style="left:${pos}%;background:${color}"></i></div>
          <div class="ac-meta">${anQual(v)} · 均值 ${anVal(s.mean)}</div>
        </div>`;
      })
      .join("");
  }

  function renderPairs(d) {
    const wrap = $("#an-pairs-wrap");
    if (!d.pairs || d.pairs.length < 2) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    $("#an-pairs-sub").textContent =
      d.pairs.length > 12 ? `${d.pairs.length} 对 · 显示 |ρ| 前 12` : `${d.pairs.length} 对`;
    const rows = [...d.pairs]
      .sort((x, y) => Math.abs(y.pearson ?? 0) - Math.abs(x.pearson ?? 0))
      .slice(0, 12)
      .map((p) => {
        const cell = (v) =>
          `<td style="color:${v == null ? "#5d6679" : v > 0 ? "#ff5468" : "#19c48a"}">${anVal(v)}</td>`;
        return `<tr><td>${p.a_name} × ${p.b_name}</td>${cell(p.pearson)}${cell(p.spearman)}${cell(p.kendall)}</tr>`;
      })
      .join("");
    $("#an-pairs").innerHTML = `<table class="an-table">
      <thead><tr><th>配对</th><th>Pearson</th><th>Spearman</th><th>Kendall</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  function renderScatter(d) {
    const wrap = $("#an-scatter-wrap");
    if (!d.scatter) {
      wrap.hidden = true;
      updateLowerLayout();
      return;
    }
    wrap.hidden = false;
    $("#an-scatter-sub").textContent = `${d.scatter.a_name} × ${d.scatter.b_name} · ${d.scatter.points.length} 日`;
    if (!an.scatter) an.scatter = echarts.init($("#an-scatter"));
    const pts = d.scatter.points;
    if (!pts.length) return;
    const n = pts.length;
    const mx = pts.reduce((s, p) => s + p[0], 0) / n;
    const my = pts.reduce((s, p) => s + p[1], 0) / n;
    let sxy = 0;
    let sxx = 0;
    pts.forEach((p) => {
      sxy += (p[0] - mx) * (p[1] - my);
      sxx += (p[0] - mx) ** 2;
    });
    const slope = sxx ? sxy / sxx : 0;
    const intercept = my - slope * mx;
    const xs = pts.map((p) => p[0]);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    const pctFmt = (v) => (v * 100).toFixed(1) + "%";

    an.scatter.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { left: 48, right: 16, top: 14, bottom: 34 },
        tooltip: {
          trigger: "item",
          backgroundColor: "rgba(12,17,27,.96)",
          borderColor: "rgba(255,255,255,.09)",
          textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
          formatter: (p) =>
            p.seriesType === "scatter"
              ? `${d.scatter.b_name} ${pctFmt(p.value[0])}<br/>${d.scatter.a_name} ${pctFmt(p.value[1])}`
              : "",
        },
        xAxis: {
          type: "value",
          name: d.scatter.b_name,
          nameLocation: "middle",
          nameGap: 22,
          nameTextStyle: { color: "#5d6679", fontSize: 10 },
          axisLine: { lineStyle: { color: "rgba(255,255,255,.10)" } },
          axisLabel: { ...AXIS_LABEL, formatter: pctFmt },
          splitLine: SPLIT_LINE,
        },
        yAxis: {
          type: "value",
          name: d.scatter.a_name,
          nameLocation: "middle",
          nameGap: 38,
          nameTextStyle: { color: "#5d6679", fontSize: 10 },
          axisLine: { show: false },
          axisLabel: { ...AXIS_LABEL, formatter: pctFmt },
          splitLine: SPLIT_LINE,
        },
        series: [
          {
            type: "scatter",
            data: pts,
            symbolSize: 5,
            itemStyle: { color: "rgba(125,211,252,.55)", borderColor: "rgba(125,211,252,.8)", borderWidth: 0.5 },
          },
          {
            type: "line",
            data: [
              [x0, slope * x0 + intercept],
              [x1, slope * x1 + intercept],
            ],
            showSymbol: false,
            silent: true,
            lineStyle: { color: "rgba(255,84,104,.75)", width: 1.4, type: "dashed" },
          },
        ],
      },
      true
    );
    updateLowerLayout();
  }

  function updateLowerLayout() {
    const wrap = document.querySelector(".analysis-lower");
    if (!wrap) return;
    const visible = [...wrap.children].filter((el) => !el.hidden).length;
    wrap.classList.toggle("single", visible <= 1);
  }

  function switchView(name) {
    document.querySelectorAll("#view-tabs button").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === name);
    });
    $("#view-market").hidden = name !== "market";
    $("#view-sectors").hidden = name !== "sectors";
    $("#view-macro").hidden = name !== "macro";
    $("#view-bonds").hidden = name !== "bonds";
    $("#view-analysis").hidden = name !== "analysis";
    try {
      const url = new URL(location.href);
      if (name !== "market") url.searchParams.set("view", name);
      else url.searchParams.delete("view");
      history.replaceState(null, "", url);
    } catch (_) {
      /* 忽略 */
    }
    if (name === "sectors") {
      initSectors();
      setTimeout(() => sec.chart && sec.chart.resize(), 60);
    } else if (name === "macro") {
      initMacro();
    } else if (name === "bonds") {
      initBonds();
      setTimeout(() => Object.values(bd.charts).forEach((c) => c.resize()), 80);
    } else if (name === "analysis") {
      initAnalysis();
      if (!an.chart) an.chart = echarts.init($("#an-chart"));
      if (typeof ResizeObserver !== "undefined" && !an.observer) {
        an.observer = new ResizeObserver(() => an.chart && an.chart.resize());
        an.observer.observe($("#an-chart"));
      }
      if (!an.data && an.ids.length >= 2) runAnalysis();
      setTimeout(() => {
        an.chart && an.chart.resize();
        an.scatter && an.scatter.resize();
      }, 60);
    } else if (state.chart) {
      state.chart.resize();
    }
  }

  /* ─────────────────────────── 行业热力图 ─────────────────────────── */
  const sec = { list: [], node: "", keyword: "", sortAsc: false, chart: null, bound: false, board: null, market: "cn" };

  function secColor(pct) {
    const p = Math.max(-6, Math.min(6, pct || 0));
    const t = Math.abs(p) / 6;
    const base = p >= 0 ? [229, 72, 93] : [26, 156, 110];
    const alpha = 0.35 + 0.5 * t;
    return `rgba(${base[0]},${base[1]},${base[2]},${alpha.toFixed(2)})`;
  }

  function initSectors() {
    if (!sec.chart) {
      const el = $("#sec-chart");
      if (el && el.clientHeight > 10) sec.chart = echarts.init(el);
    }
    if (!sec.bound) {
      sec.bound = true;
      $("#sec-market").addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-market]");
        if (!btn || btn.dataset.market === sec.market) return;
        sec.market = btn.dataset.market;
        $("#sec-market").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
        sec.list = [];
        sec.node = "";
        closeStock();
        loadSectors();
      });
      $("#sec-list").addEventListener("click", (e) => {
        const item = e.target.closest(".sec-item");
        if (item && item.dataset.node && item.dataset.node !== sec.node) {
          selectIndustry(item.dataset.node);
        }
      });
      $("#sec-search").addEventListener("input", (e) => {
        sec.keyword = e.target.value.trim();
        renderSecList();
      });
      $("#sec-sort").addEventListener("click", () => {
        sec.sortAsc = !sec.sortAsc;
        $("#sec-sort").textContent = sec.sortAsc ? "按涨幅 ↑" : "按涨幅 ↓";
        renderSecList();
      });
    }
    if (!sec.list.length) loadSectors();
  }

  async function loadSectors() {
    $("#sec-list").innerHTML = `<div class="sec-empty">行业数据加载中…</div>`;
    try {
      const res = await fetch(`/api/sectors?market=${sec.market}`, { cache: "no-store" });
      const data = await res.json();
      sec.list = data.industries || [];
      if (!sec.list.length) {
        $("#sec-list").innerHTML = `<div class="sec-empty">暂无行业数据</div>`;
        return;
      }
      renderSecList();
      if (!sec.node) {
        const preferred =
          sec.list.find((i) => i.name.includes("电子信息")) ||
          sec.list.slice().sort((a, b) => b.count - a.count)[0];
        selectIndustry(preferred.node);
      }
    } catch (err) {
      $("#sec-list").innerHTML = `<div class="sec-empty">加载失败：${err.message}</div>`;
    }
  }

  function renderSecList() {
    const kw = sec.keyword;
    const rows = sec.list.filter((i) => !kw || i.name.includes(kw));
    rows.sort((a, b) => (sec.sortAsc ? a.pct - b.pct : b.pct - a.pct));
    $("#sec-list").innerHTML = rows
      .map(
        (i) => `<button class="sec-item ${i.node === sec.node ? "active" : ""}" data-node="${i.node}">
          <span class="sec-name">${i.name}</span>
          <span class="sec-count">${i.count}</span>
          <span class="sec-pct ${i.pct >= 0 ? "up" : "down"}">${i.pct >= 0 ? "+" : ""}${i.pct.toFixed(2)}%</span>
        </button>`
      )
      .join("");
  }

  async function selectIndustry(node) {
    closeStock();
    sec.node = node;
    renderSecList();
    const info = sec.list.find((i) => i.node === node);
    $("#sec-sub").textContent = `${info ? info.name : node} · 成分股加载中…`;
    try {
      const res = await fetch(`/api/sectors/board?node=${encodeURIComponent(node)}&limit=50&market=${sec.market}`);
      sec.board = await res.json();
      renderTreemap(sec.board);
    } catch (err) {
      $("#sec-sub").textContent = "成分股加载失败：" + err.message;
    }
  }

  function renderTreemap(data) {
    const members = data.members || [];
    if (!sec.chart) sec.chart = echarts.init($("#sec-chart"));
    if (!members.length) {
      sec.chart.clear();
      $("#sec-sub").textContent = `${data.name || ""} 暂无成分股数据`;
      $("#sec-badges").innerHTML = "";
      return;
    }
    const total = members.reduce((a, m) => a + (m.mktcap || 0), 0);
    $("#sec-sub").textContent = secSubText(data);
    $("#sec-badges").innerHTML = [
      `<span class="chip">行业涨跌 <b class="${data.pct >= 0 ? "up" : "down"}">${data.pct >= 0 ? "+" : ""}${data.pct}%</b></span>`,
      sec.market === "us"
        ? `<span class="chip muted">美股 · 按 sector 分类</span>`
        : data.etf
          ? `<span class="chip">行业ETF ${data.etf.name} <b class="${data.etf.pct >= 0 ? "up" : "down"}">${data.etf.pct >= 0 ? "+" : ""}${data.etf.pct}%</b></span>`
          : `<span class="chip muted">未匹配到行业 ETF</span>`,
      `<span class="chip muted">点击方块可跳转行情</span>`,
    ].join("");

    sec.chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        tooltip: {
          backgroundColor: "rgba(12,17,27,.96)",
          borderColor: "rgba(255,255,255,.09)",
          borderWidth: 1,
          padding: [8, 11],
          textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
          formatter: (p) => {
            const d = p.data || {};
            if (d.code == null) return "";
            const pct = d.pct == null ? "—" : `${d.pct >= 0 ? "+" : ""}${d.pct.toFixed(2)}%`;
            return `<div style="color:#8b95a9">${d.code}</div><b>${d.name}</b><br/>
              市值 ${secCapText(d.value)}<br/>
              <span style="color:${(d.pct || 0) >= 0 ? UP : DOWN}">${pct}</span>`;
          },
        },
        series: [
          {
            type: "treemap",
            roam: false,
            nodeClick: false,
            breadcrumb: { show: false },
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
            width: "100%",
            height: "100%",
            itemStyle: { borderColor: "#080c14", borderWidth: 1, gapWidth: 1 },
            label: {
              show: true,
              color: "#f2f6ff",
              fontSize: 11,
              lineHeight: 14,
              formatter: (p) =>
                p.data && p.data.pct != null
                  ? `${p.data.name}\n${p.data.pct >= 0 ? "+" : ""}${p.data.pct.toFixed(1)}%`
                  : p.name,
            },
            data: members.map((m) => ({
              name: m.name,
              value: Math.max(m.mktcap || 0, 1),
              pct: m.pct,
              code: m.code,
              itemStyle: { color: secColor(m.pct) },
            })),
          },
        ],
      },
      true
    );
    sec.chart.off("click");
    sec.chart.on("click", (params) => {
      const d = params.data || {};
      if (!d.code) return;
      openStock(String(d.code).toLowerCase(), d.name);
    });
  }

  /* ─────────────────────────── 个股详情弹窗 ─────────────────────────── */
  const stk = { charts: {}, bound: false };

  function stkChart(id) {
    if (!stk.charts[id]) {
      const el = $("#" + id);
      if (el) stk.charts[id] = echarts.init(el);
    }
    return stk.charts[id];
  }

  function secCapText(v) {
    if (sec.market === "us") return `$${(v / 1e12).toFixed(2)} 万亿`;
    return `${(v / 1e8).toFixed(0)} 亿`;
  }

  function secSubText(data) {
    if (!data) return "选择左侧行业";
    const rows = data.members || [];
    const total = rows.reduce((a, m) => a + (m.mktcap || 0), 0);
    return `${data.name} · 共 ${data.count} 只 · 前 ${rows.length} 只合计 ${secCapText(total)}`;
  }


  /* 左侧行业列表高度与右侧内容（热力图 / 个股面板）对齐 */
  function syncSecListHeight() {
    const list = $("#sec-list");
    if (!list) return;
    const panel = $("#stk-panel");
    const treemap = $("#sec-chart");
    const open = panel && !panel.hidden;
    const target = open ? panel.offsetHeight : (treemap && treemap.offsetHeight) || 620;
    list.style.maxHeight = Math.max(320, target) + "px";
  }

  function closeStock() {
    const panel = $("#stk-panel");
    const treemap = $("#sec-chart");
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    treemap.hidden = false;
    $("#sec-badges").hidden = false;
    $("#sec-sub").textContent = secSubText(sec.board);
    syncSecListHeight();
    if (sec.chart) setTimeout(() => sec.chart.resize(), 30);
  }

  async function openStock(code, name) {
    const panel = $("#stk-panel");
    const treemap = $("#sec-chart");
    if (!panel) return;
    treemap.hidden = true;
    $("#sec-badges").hidden = true;
    panel.hidden = false;
    if (!stk.bound) {
      stk.bound = true;
      $("#stk-back").addEventListener("click", closeStock);
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !panel.hidden) closeStock();
      });
    }
    $("#st-name").textContent = name || code;
    $("#st-code").textContent = code;
    $("#st-price").textContent = "…";
    $("#st-pct").textContent = "";
    $("#st-meta").textContent = "加载中…";
    $("#sec-sub").textContent = `${name || code} · ${code} · 点「返回板块」回到热力图`;
    Object.values(stk.charts).forEach((c) => c.resize());
    try {
      const res = await fetch(`/api/stock?code=${encodeURIComponent(code)}`, { cache: "no-store" });
      renderStock(await res.json(), name);
      requestAnimationFrame(() => {
        Object.values(stk.charts).forEach((c) => c.resize());
        syncSecListHeight();
      });
    } catch (err) {
      $("#st-meta").textContent = "加载失败：" + err.message;
    }
  }

  const STK_AXIS = {
    axisLine: { lineStyle: { color: "rgba(255,255,255,.10)" } },
    axisTick: { show: false },
    axisLabel: { color: "#5d6679", fontSize: 9.5, fontFamily: "JetBrains Mono, Consolas, monospace", hideOverlap: true },
    splitLine: { show: false },
  };

  function renderStock(data, fallbackName) {
    const q = data.quote || {};
    const price = q.price;
    const prev = q.prev_close;
    const pct = q.pct != null ? q.pct : price != null && prev ? (price / prev - 1) * 100 : null;
    $("#st-name").textContent = data.name && data.name !== data.code ? data.name : fallbackName || data.code;
    $("#st-code").textContent = data.code;
    $("#st-price").textContent = num(price, 2);
    const pctEl = $("#st-pct");
    pctEl.textContent = pct == null ? "—" : `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
    pctEl.style.color = pct == null ? "" : pct >= 0 ? UP : DOWN;
    $("#st-meta").textContent =
      `开 ${num(q.open, 2)} · 高 ${num(q.high, 2)} · 低 ${num(q.low, 2)} · 昨收 ${num(prev, 2)}` +
      (q.volume ? ` · 量 ${fmtVol(q.volume)}` : "") +
      (q.amount ? ` · 额 ${fmtVol(q.amount)}` : "");

    renderStockIntraday(data, prev);
    renderStockKline(data);
    renderStockFlow(data);
    linkStockZoom();
  }

  let stockZoomSync = false;

  /* 日线图与资金流图双向同步缩放（两者 x 轴都是同一段 250 个交易日） */
  function linkStockZoom() {
    const kline = stk.charts["st-kline"];
    const flow = stk.charts["st-flow"];
    if (!kline || !flow) return;
    const bind = (src, dst) => {
      src.off("datazoom");
      src.on("datazoom", () => {
        if (stockZoomSync) return;
        const opt = src.getOption();
        const dz = (opt.dataZoom && opt.dataZoom[0]) || {};
        if (dz.start == null || dz.end == null) return;
        stockZoomSync = true;
        dst.dispatchAction({ type: "dataZoom", start: dz.start, end: dz.end });
        stockZoomSync = false;
      });
    };
    bind(kline, flow);
    bind(flow, kline);
  }

  function renderStockIntraday(data, prev) {
    const chart = stkChart("st-intraday");
    if (!chart) return;
    const pts = (data.intraday && data.intraday.points) || [];
    if (!pts.length) {
      chart.clear();
      chart.setOption({
        title: { text: "暂无分时数据", left: "center", top: "middle", textStyle: { color: "#5d6679", fontSize: 12, fontWeight: "normal" } },
      }, true);
      return;
    }
    const times = pts.map((p) => p[0]);
    const prices = pts.map((p) => p[1]);
    const vols = pts.map((p) => p[2]);
    const up = prices[prices.length - 1] >= (prev || prices[0]);
    const color = up ? UP : DOWN;
    chart.setOption({
      animation: false,
      backgroundColor: "transparent",
      grid: [{ left: 8, right: 54, top: 12, height: "62%" }, { left: 8, right: 54, top: "76%", height: "16%" }],
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(12,17,27,.96)",
        borderColor: "rgba(255,255,255,.09)",
        textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
      },
      xAxis: [
        { type: "category", data: times, boundaryGap: false, ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, interval: Math.max(1, Math.floor(times.length / 6)) } },
        { type: "category", gridIndex: 1, data: times, boundaryGap: true, ...STK_AXIS, axisLabel: { show: false } },
      ],
      yAxis: [
        { type: "value", position: "right", scale: true, ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => Number(v).toFixed(2) } },
        { type: "value", gridIndex: 1, scale: true, position: "right", splitNumber: 2, ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => fmtVol(v) } },
      ],
      series: [
        {
          type: "line", data: prices, showSymbol: false,
          lineStyle: { width: 1.6, color }, itemStyle: { color },
          areaStyle: { color: up ? "rgba(255,84,104,.18)" : "rgba(25,196,138,.18)" },
          markLine: prev == null ? undefined : {
            silent: true, symbol: "none", label: { show: false },
            lineStyle: { color: "rgba(255,255,255,.2)", type: "dashed" },
            data: [{ yAxis: prev }],
          },
        },
        { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols, itemStyle: { color: up ? "rgba(255,84,104,.45)" : "rgba(25,196,138,.45)" } },
      ],
    }, true);
  }

  function renderStockKline(data) {
    const chart = stkChart("st-kline");
    if (!chart) return;
    const bars = data.hist || [];
    if (!bars.length) {
      chart.clear();
      return;
    }
    const dates = bars.map((b) => b[0]);
    const ohlc = bars.map((b) => [b[1], b[4], b[3], b[2]]); // open, close, low, high
    const vols = bars.map((b) => b[5]);
    const ma20 = bars.map((_, i) => {
      const part = bars.slice(Math.max(0, i - 19), i + 1);
      return part.reduce((a, b) => a + b[4], 0) / part.length;
    });
    chart.setOption({
      animation: false,
      backgroundColor: "transparent",
      grid: [{ left: 8, right: 54, top: 12, height: "64%" }, { left: 8, right: 54, top: "78%", height: "14%" }],
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(12,17,27,.96)",
        borderColor: "rgba(255,255,255,.09)",
        textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
        formatter: (ps) => {
          const p = ps.find((x) => x.seriesType === "candlestick") || ps[0];
          if (!p) return "";
          const b = bars[p.dataIndex];
          return `<div style="color:#8b95a9;margin-bottom:3px">${b[0]}</div>
            开 ${num(b[1], 2)}　收 ${num(b[4], 2)}<br/>高 ${num(b[2], 2)}　低 ${num(b[3], 2)}<br/>量 ${fmtVol(b[5])}`;
        },
      },
      xAxis: [
        { type: "category", data: dates, ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => v.slice(2, 7), interval: Math.max(1, Math.floor(dates.length / 8)) } },
        { type: "category", gridIndex: 1, data: dates, ...STK_AXIS, axisLabel: { show: false } },
      ],
      yAxis: [
        { scale: true, position: "right", ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => Number(v).toFixed(2) } },
        { gridIndex: 1, scale: true, position: "right", splitNumber: 2, ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => fmtVol(v) } },
      ],
      dataZoom: [{ type: "inside", start: 0, end: 100 }],
      series: [
        {
          type: "candlestick", data: ohlc,
          itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
        },
        { type: "line", data: ma20, showSymbol: false, lineStyle: { width: 1, color: "#7dd3fc" }, itemStyle: { color: "#7dd3fc" } },
        { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols, itemStyle: { color: "rgba(140,160,190,.45)" } },
      ],
    }, true);
  }

  function renderStockFlow(data) {
    const chart = stkChart("st-flow");
    if (!chart) return;
    const rows = data.flow || [];
    if (!rows.length) {
      chart.clear();
      return;
    }
    const dates = rows.map((r) => r[0]);
    const net = rows.map((r) => r[3]);
    let acc = 0;
    const cum = net.map((v) => (acc += v));
    chart.setOption({
      animation: false,
      backgroundColor: "transparent",
      grid: { left: 8, right: 54, top: 12, bottom: 26 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(12,17,27,.96)",
        borderColor: "rgba(255,255,255,.09)",
        textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
      },
      dataZoom: [{ type: "inside", start: 0, end: 100 }],
      xAxis: [{ type: "category", data: dates, ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => v.slice(2, 7), interval: Math.max(1, Math.floor(dates.length / 8)) } }],
      yAxis: [
        { type: "value", min: -100, max: 100, position: "right", ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => v + "%" } },
        { type: "value", position: "left", scale: true, ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => v.toFixed(0) } },
      ],
      series: [
        {
          type: "bar",
          data: net.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? "rgba(255,84,104,.7)" : "rgba(25,196,138,.7)" } })),
          barMaxWidth: 6,
        },
        { type: "line", data: cum, yAxisIndex: 1, showSymbol: false, lineStyle: { width: 1.5, color: "#5eead4" }, itemStyle: { color: "#5eead4" } },
      ],
    }, true);
  }


  /* ─────────────────────────── 全球宏观 ─────────────────────────── */
  const mac = { data: null, sortKey: "gdp", sortAsc: false, loading: false, bound: false };

  const fmtTrl = (v) => (v == null ? "—" : (v / 1e12).toFixed(2) + " 万亿");
  const fmtPct1 = (v) => (v == null ? "—" : v.toFixed(1) + "%");
  const fmtUsd = (v) => (v == null ? "—" : "$" + Math.round(v).toLocaleString("en-US"));
  const fmtPop = (v) => (v == null ? "—" : (v / 1e8).toFixed(2) + " 亿");

  function initMacro() {
    if (!mac.bound) {
      mac.bound = true;
      $("#macro-refresh").addEventListener("click", () => loadMacro(true));
      $("#macro-table").addEventListener("click", (e) => {
        const th = e.target.closest("th[data-key]");
        if (!th) return;
        const key = th.dataset.key;
        mac.sortAsc = mac.sortKey === key ? !mac.sortAsc : false;
        mac.sortKey = key;
        renderMacro();
      });
    }
    if (!mac.data) loadMacro();
  }

  async function loadMacro(force) {
    if (mac.loading) return;
    mac.loading = true;
    $("#macro-sub").textContent = "宏观数据加载中（世界银行年度数据，首次约 3~8 秒）…";
    try {
      const res = await fetch("/api/macro" + (force ? "?refresh=1" : ""), { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      mac.data = await res.json();
      renderMacro();
    } catch (err) {
      $("#macro-sub").textContent = "加载失败：" + err.message;
    } finally {
      mac.loading = false;
    }
  }

  function renderMacro() {
    const d = mac.data;
    if (!d || !d.rows) return;
    const rows = [...d.rows];
    const key = mac.sortKey;
    rows.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return mac.sortAsc ? av - bv : bv - av;
    });
    const arrow = (k) => (mac.sortKey === k ? (mac.sortAsc ? " ↑" : " ↓") : "");
    const cols = [
      ["gdp", "GDP", "万亿"],
      ["gdp_yoy", "GDP 同比", "%"],
      ["gni", "GNI", "万亿"],
      ["gdp_pc", "人均 GDP", "美元"],
      ["unemployment", "失业率", "%"],
      ["debt", "政府债务/GDP", "%"],
      ["m2", "广义货币/GDP", "%"],
      ["cpi", "CPI 同比", "%"],
      ["rate", "政策利率", "%"],
    ];
    const head = `<thead><tr><th>#</th><th>经济体</th>${cols
      .map(([k, label]) => `<th data-key="${k}">${label}${arrow(k)}</th>`)
      .join("")}<th>数据年</th></tr></thead>`;
    const body = rows
      .map((r, i) => {
        const yoyClass = (r.gdp_yoy || 0) >= 0 ? "up" : "down";
        return `<tr>
          <td class="idx">${i + 1}</td>
          <td class="name">${r.name}</td>
          <td>${fmtTrl(r.gdp)}</td>
          <td class="${yoyClass}">${fmtPct1(r.gdp_yoy)}</td>
          <td>${fmtTrl(r.gni)}</td>
          <td>${fmtUsd(r.gdp_pc)}</td>
          <td>${fmtPct1(r.unemployment)}</td>
          <td title="${r.debt_source || ""}${r.debt_as_of ? " · " + r.debt_as_of : ""}">${fmtPct1(r.debt)}${r.debt_source && r.debt_source !== "世界银行" ? `<i class="src">${r.debt_source.indexOf("本地") === 0 ? "本" : "官"}</i>` : ""}</td>
          <td>${fmtPct1(r.m2)}</td>
          <td class="${(r.cpi || 0) >= 0 ? "up" : "down"}">${fmtPct1(r.cpi)}</td>
          <td>${r.rate == null ? "—" : r.rate.toFixed(2) + "%"}</td>
          <td class="idx">${(r.years && r.years.gdp) || "—"}</td>
        </tr>`;
      })
      .join("");
    $("#macro-table").innerHTML = head + `<tbody>${body}</tbody>`;
    $("#macro-sub").textContent =
      `更新于 ${d.updated_at} · 政策利率为手动维护参考值（${d.policy_as_of}）· 带「官」的债务数据来自该国官方统计源 · ${d.source}`;
  }


  /* ─────────────────────────── 债券市场 ─────────────────────────── */
  const bd = { data: null, charts: {}, bound: false, loading: false };

  function initBonds() {
    if (!bd.bound) {
      bd.bound = true;
      $("#bonds-refresh").addEventListener("click", () => loadBonds(true));
    }
    if (!bd.data) loadBonds();
  }

  async function loadBonds(force) {
    if (bd.loading) return;
    bd.loading = true;
    $("#bonds-sub").textContent = "加载中…";
    try {
      const res = await fetch("/api/bonds" + (force ? "?refresh=1" : ""), { cache: "no-store" });
      bd.data = await res.json();
      renderBonds();
    } catch (err) {
      $("#bonds-sub").textContent = "加载失败：" + err.message;
    } finally {
      bd.loading = false;
    }
  }

  function bondChart(id) {
    if (!bd.charts[id]) {
      const el = $("#" + id);
      if (el) bd.charts[id] = echarts.init(el);
    }
    return bd.charts[id];
  }

  function renderBonds() {
    const d = bd.data || {};
    const curves = d.curves || {};
    const terms = ["2Y", "5Y", "10Y", "30Y"];
    const curve = bondChart("bond-curve");
    if (curve) {
      const pick = (m) => {
        const rows = curves[m] || [];
        const map = new Map(rows.map((r) => [r.term, r.value]));
        return terms.map((t) => (map.has(t) ? map.get(t) : null));
      };
      curve.setOption({
        animation: false,
        backgroundColor: "transparent",
        grid: { left: 44, right: 30, top: 34, bottom: 28 },
        legend: { top: 0, left: 0, itemWidth: 14, itemHeight: 3, textStyle: { color: "#8b95a9", fontSize: 10.5 } },
        tooltip: {
          trigger: "axis",
          backgroundColor: "rgba(12,17,27,.96)",
          borderColor: "rgba(255,255,255,.09)",
          textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
          valueFormatter: (v) => (v == null ? "—" : v.toFixed(3) + "%"),
        },
        xAxis: { type: "category", data: terms, ...STK_AXIS },
        yAxis: {
          type: "value", scale: true, position: "right", ...STK_AXIS,
          axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => v.toFixed(1) + "%" },
        },
        series: [
          { name: "中国国债", type: "line", data: pick("CN"), connectNulls: true, symbolSize: 7, lineStyle: { width: 2, color: "#ff5468" }, itemStyle: { color: "#ff5468" } },
          { name: "美国国债", type: "line", data: pick("US"), connectNulls: true, symbolSize: 7, lineStyle: { width: 2, color: "#7dd3fc" }, itemStyle: { color: "#7dd3fc" } },
        ],
      }, true);
    }

    const iss = d.issuance || {};
    const issue = bondChart("bond-issue");
    if (issue) {
      const colors = { Bill: "#f6c76b", Note: "#7dd3fc", Bond: "#a78bfa", TIPS: "#5eead4", FRN: "#f9a8d4" };
      issue.setOption({
        animation: false,
        backgroundColor: "transparent",
        grid: { left: 44, right: 30, top: 34, bottom: 28 },
        legend: { top: 0, left: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: "#8b95a9", fontSize: 10.5 } },
        tooltip: {
          trigger: "axis",
          backgroundColor: "rgba(12,17,27,.96)",
          borderColor: "rgba(255,255,255,.09)",
          textStyle: { color: "#dfe6f3", fontSize: 11, fontFamily: "JetBrains Mono, Consolas, monospace" },
        },
        xAxis: { type: "category", data: iss.months || [], ...STK_AXIS },
        yAxis: { type: "value", position: "right", ...STK_AXIS, axisLabel: { ...STK_AXIS.axisLabel, formatter: (v) => v.toFixed(0) } },
        series: (iss.types || []).map((t) => ({
          name: t,
          type: "bar",
          stack: "amt",
          data: (iss.series || {})[t] || [],
          itemStyle: { color: colors[t] || "#8b95a9" },
          barMaxWidth: 26,
        })),
      }, true);
    }

    const parts = [];
    const src = d.sources || {};
    if (curves.CN && curves.CN.length) {
      const c = curves.CN.find((p) => p.term === "10Y");
      if (c) parts.push(`中债 10Y ${c.value.toFixed(3)}%`);
    } else {
      parts.push("中债收益率暂缺");
    }
    if (curves.US && curves.US.length) {
      const c = curves.US.find((p) => p.term === "10Y");
      if (c) parts.push(`美债 10Y ${c.value.toFixed(3)}%`);
    } else {
      parts.push("⚠ 美债收益率暂缺（实时源不可用且无本地数据）");
    }
    parts.push(`数据: 中债 ${src.CN || "—"} / 美债 ${src.US || "—"}`);
    parts.push(`更新于 ${d.updated_at || "—"}`);
    parts.push("中债发行量暂无稳定免费源");
    $("#bonds-sub").textContent = parts.join(" · ");
  }

  function bindAnalysis() {
    document.querySelectorAll("#view-tabs button").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view));
    });
    $("#asset-list").addEventListener("change", (e) => {
      const row = e.target.closest(".pick-row");
      if (row) toggleAsset(row.dataset.id);
    });
    $("#picked-chips").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-remove]");
      if (btn) toggleAsset(btn.dataset.remove);
    });
    $("#asset-search").addEventListener("input", (e) => buildPickList(e.target.value));
    $("#window-seg").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-window]");
      if (!btn) return;
      an.window = Number(btn.dataset.window);
      $("#window-seg").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      scheduleAnalysis();
    });
    $("#analysis-range").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-range]");
      if (!btn) return;
      an.range = btn.dataset.range;
      $("#analysis-range").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      scheduleAnalysis();
    });
    $("#btn-an-reload").addEventListener("click", () => {
      an.data = null;
      runAnalysis();
    });
    $("#btn-weights").addEventListener("click", () => {
      const panel = $("#weight-panel");
      panel.hidden = !panel.hidden;
      $("#btn-weights").setAttribute("aria-expanded", String(!panel.hidden));
      $("#btn-weights").classList.toggle("active", !panel.hidden);
      if (!panel.hidden && !panel.children.length) buildWeightPanel();
    });
    $("#btn-weights-reset").addEventListener("click", () => {
      an.weights = { ...AN_DEFAULT_WEIGHTS };
      buildWeightPanel();
      saveAnWeights();
      scheduleAnalysis();
    });
    $("#weight-panel").addEventListener("input", (e) => {
      const row = e.target.closest(".wp-row");
      if (!row) return;
      an.weights[row.dataset.key] = Number(e.target.value) / 100;
      updateWeightHint();
      saveAnWeights();
      scheduleAnalysis();
    });
    window.addEventListener("resize", () => {
      if ($("#view-analysis").hidden) return;
      an.chart && an.chart.resize();
      an.scatter && an.scatter.resize();
    });
  }

  /* ─────────────────────────── 启动 ───────────────────────────── */
  function bind() {
    $("#btn-refresh").addEventListener("click", manualRefresh);
    $("#btn-download").addEventListener("click", startDownload);
    $("#btn-download-one").addEventListener("click", startDownload);
    $("#btn-csv").addEventListener("click", () => {
      window.open(`/api/export?id=${encodeURIComponent(state.selected)}&interval=day`, "_blank");
    });
    $("#interval").addEventListener("change", (e) => {
      state.intervalMs = Number(e.target.value);
      schedule();
      toast(state.intervalMs ? `刷新频率 ${state.intervalMs / 1000}s` : "已暂停自动刷新");
    });
    $("#ranges").addEventListener("click", (e) => {
      const modeBtn = e.target.closest("button[data-mode]");
      const rangeBtn = e.target.closest("button[data-range]");
      if (!modeBtn && !rangeBtn) return;
      if (modeBtn) {
        state.mode = modeBtn.dataset.mode;
      } else {
        state.mode = "kline";
        state.range = rangeBtn.dataset.range;
      }
      const target = modeBtn || rangeBtn;
      $("#ranges").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === target));
      state.chartKey = "";
      loadChart(true);
    });
    $("#search").addEventListener("input", (e) => applySearch(e.target.value));

    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && document.activeElement !== $("#search")) {
        e.preventDefault();
        $("#search").focus();
      } else if (e.key === "r" && !e.ctrlKey && !e.metaKey && document.activeElement.tagName !== "INPUT") {
        manualRefresh();
      } else if (e.key === "Escape") {
        $("#search").value = "";
        applySearch("");
        $("#search").blur();
      }
    });

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && state.intervalMs) {
        clearTimeout(state.timer);
        fetchOverview();
      }
    });
  }

  function boot() {
    bind();
    bindAnalysis();
    // 心跳：桌面版据此判断窗口是否还开着（浏览器启动进程会立刻退出，不能当窗口生命周期用）
    const ping = () => fetch("/api/ping", { cache: "no-store" }).catch(() => {});
    ping();
    setInterval(ping, 5000);
    const hash = decodeURIComponent((location.hash || "").slice(1));
    if (hash) state.selected = hash;
    tickClock();
    setInterval(tickClock, 1000);
    initChart();
    fetchOverview().then(() => loadChart(true));
    fetchMarketFlow();
    window.addEventListener("hashchange", () => {
      const id = decodeURIComponent(location.hash.slice(1));
      if (id && id !== state.selected) selectAsset(id, { scroll: false });
    });
    try {
      const params = new URLSearchParams(location.search);
      if (params.get("view") === "analysis") switchView("analysis");
      if (params.get("view") === "sectors") switchView("sectors");
      if (params.get("view") === "macro") switchView("macro");
      if (params.get("view") === "bonds") switchView("bonds");
      const modeParam = params.get("mode");
      if (modeParam === "intraday" || modeParam === "flow") {
        state.mode = modeParam;
        const btn = document.querySelector(`#ranges button[data-mode="${modeParam}"]`);
        if (btn) {
          $("#ranges").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
        }
      }
    } catch (_) {
      /* 忽略 */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
