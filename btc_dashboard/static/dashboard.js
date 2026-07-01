let feeChart;
let hashChart;
let priceChart;
let txChart;
let etfFlowChart;
let supplyOwnershipChart;
let mvrvChart;
let btcTrendTimeframe = "1d";
let mvrvHistoryLoaded = false;
let mvrvHistoryLoading = false;
let etfFlowChartLoaded = false;
let etfFlowChartLoading = false;
let supplyOwnershipLoaded = false;
let supplyOwnershipLoading = false;
const refreshJobs = new Map();

const sharedChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { intersect: false, mode: "index" },
    plugins: { legend: { labels: { color: "#d1d5db" } } },
    scales: {
        x: { ticks: { color: "#9ca3af", maxRotation: 0 }, grid: { color: "rgba(75, 85, 99, 0.35)" } },
        y: { beginAtZero: true, ticks: { color: "#9ca3af" }, grid: { color: "rgba(75, 85, 99, 0.35)" } },
    },
};

async function fetchJson(url) {
    try {
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) throw new Error(`Request failed: ${response.status}`);
        return response.json();
    } catch (error) {
        console.error(`Failed to fetch ${url}`, error);
        throw error;
    }
}

function valueOrNA(value) {
    if (value === undefined || value === null || value === "" || value === "unknown") return "N/A";
    if (typeof value === "number" && Number.isNaN(value)) return "N/A";
    return value;
}

function formatUsd(value) {
    if (value === undefined || value === null || value === "" || value === "N/A") return "N/A";
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `$${numeric.toLocaleString()}` : `$${value}`;
}

function formatSignedUsd(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "N/A";
    const sign = numeric > 0 ? "+" : "";
    return `${sign}$${Math.abs(numeric).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSignedPercent(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "N/A";
    const sign = numeric > 0 ? "+" : "";
    return `${sign}${numeric.toFixed(2)}%`;
}

function formatCompactUsd(value) {
    if (value === undefined || value === null || value === "" || value === "N/A") return "N/A";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "N/A";
    const sign = numeric < 0 ? "-" : "";
    const abs = Math.abs(numeric);
    if (abs >= 1_000_000_000_000) return `${sign}$${(abs / 1_000_000_000_000).toFixed(2)}T`;
    if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(2)}B`;
    if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
    if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(2)}K`;
    return `${sign}$${abs.toFixed(2)}`;
}

function formatSignedCompactUsd(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "N/A";
    const sign = numeric > 0 ? "+" : numeric < 0 ? "-" : "";
    const abs = Math.abs(numeric);
    const compactNumber = (scaled) => scaled.toLocaleString(undefined, {
        maximumFractionDigits: 2,
    });
    if (abs >= 1_000_000_000_000) return `${sign}$${compactNumber(abs / 1_000_000_000_000)}T`;
    if (abs >= 1_000_000_000) return `${sign}$${compactNumber(abs / 1_000_000_000)}B`;
    if (abs >= 1_000_000) return `${sign}$${compactNumber(abs / 1_000_000)}M`;
    if (abs >= 1_000) return `${sign}$${compactNumber(abs / 1_000)}K`;
    return `${sign}$${compactNumber(abs)}`;
}

function formatBtc(value) {
    if (value === undefined || value === null || value === "" || value === "N/A") return "N/A";
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${numeric.toLocaleString(undefined, { maximumFractionDigits: 0 })} BTC` : "N/A";
}

function formatPercent(value) {
    if (value === undefined || value === null || value === "" || value === "N/A") return "N/A";
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${numeric.toFixed(2)}%` : "N/A";
}

function formatInteger(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toLocaleString(undefined, { maximumFractionDigits: 0 }) : "N/A";
}

function formatFlowDate(value) {
    if (!value) return "";
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return new Date(numeric).toLocaleDateString();
    return String(value);
}

function formatDateTime(value) {
    if (!value) return "N/A";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function formatChartTimeLabel(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const now = new Date();
    const sameYear = date.getUTCFullYear() === now.getUTCFullYear();
    const dateLabel = date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        ...(sameYear ? {} : { year: "numeric" }),
    });
    const timeLabel = date.toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
        hour12: false,
        timeZoneName: "short",
    });
    return `${dateLabel} ${timeLabel}`;
}

function formatMinutesAgo(value) {
    if (!value) return "Updated: N/A";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Updated: N/A";
    const minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
    return `Updated: ${minutes} minute${minutes === 1 ? "" : "s"} ago`;
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

async function copyText(button, value) {
    if (!button || !value) return;
    try {
        await navigator.clipboard.writeText(value);
        const originalText = button.textContent;
        button.textContent = "Copied!";
        window.setTimeout(() => { button.textContent = originalText; }, 1600);
    } catch (error) {
        console.error("Failed to copy donation value", error);
        button.textContent = "Copy failed";
        window.setTimeout(() => { button.textContent = "Copy"; }, 1600);
    }
}

function initDonationBox() {
    const qrImage = document.getElementById("lightningQrImage");
    const qrFallback = document.getElementById("lightningQrFallback");
    if (qrImage && qrFallback) {
        qrImage.addEventListener("load", () => {
            qrImage.classList.remove("hidden");
            qrFallback.classList.add("hidden");
        });
        qrImage.addEventListener("error", () => {
            qrImage.removeAttribute("src");
            qrImage.classList.add("hidden");
            qrFallback.classList.remove("hidden");
            qrFallback.textContent = "Lightning QR coming soon";
        });
        if (qrImage.complete && qrImage.naturalWidth > 0) {
            qrImage.classList.remove("hidden");
            qrFallback.classList.add("hidden");
        } else if (qrImage.complete) {
            qrImage.classList.add("hidden");
            qrFallback.classList.remove("hidden");
        }
    }

    ["copyBtcDonationAddress", "copyLightningDonationAddress"].forEach((buttonId) => {
        const copyButton = document.getElementById(buttonId);
        const addressEl = document.getElementById(copyButton?.dataset.copyTarget || "");
        copyButton?.addEventListener("click", () => {
            const address = addressEl?.textContent?.trim();
            if (address) copyText(copyButton, address);
        });
    });
}

function riskClass(level) {
    const map = { safe: "risk-safe", low: "risk-low", medium: "risk-medium", high: "risk-high", critical: "risk-critical", unknown: "text-gray-400" };
    return map[level] || "text-gray-400";
}

function riskLabel(level) {
    const map = { safe: "LOW", low: "LOW", medium: "MEDIUM", high: "HIGH", critical: "CRITICAL", unknown: "LOW" };
    return map[level] || level?.toUpperCase() || "-";
}

function normalizeRisk(level) {
    return level || "low";
}

function hasRpcSecurityData(metric) {
    return true;
}

function worstSecurityRisk(data) {
    const priority = { unknown: 0, safe: 1, low: 1, medium: 2, high: 3, critical: 4 };
    const risks = [
        normalizeRisk(data?.double_spend?.risk_level),
        normalizeRisk(data?.attack_51?.risk_level),
        normalizeRisk(data?.invalid_blocks?.risk_level),
        normalizeRisk(data?.reorgs?.risk_level),
    ];
    return risks.reduce((worst, risk) => (priority[risk] > priority[worst] ? risk : worst), "unknown");
}

function updateNetworkSecuritySummary(data) {
    const status = worstSecurityRisk(data);
    const attackData = data?.attack_51 ?? {};
    const topPool = attackData.pools?.[0] ?? {};
    const topPoolShare = attackData.top_pool_share ?? topPool.share;
    const topPoolName = topPool.name ?? "Unknown pool";
    const attackRisk = attackData.risk_level ?? "unknown";
    const statusEl = document.getElementById("networkSecurityStatus");
    const summaryEl = document.getElementById("networkSecuritySummary");
    if (!statusEl || !summaryEl) return;
    statusEl.textContent = riskLabel(status);
    statusEl.className = `text-xs font-semibold ${riskClass(status)}`;
    summaryEl.innerHTML = `
        <span class="block font-semibold ${riskClass(attackRisk)}">
            51% Attack Risk: ${escapeHtml(topPoolShare ?? "-")}% ${escapeHtml(topPoolName)}
            ${escapeHtml(riskLabel(attackRisk))}
        </span>
        <span class="block">
            Double-spend: ${escapeHtml(riskLabel(data?.double_spend?.risk_level ?? "low"))}
            | Invalid: ${escapeHtml(riskLabel(data?.invalid_blocks?.risk_level ?? "low"))}
            | Reorg: ${escapeHtml(riskLabel(data?.reorgs?.risk_level ?? "low"))}
        </span>
    `;
}

async function updateSecurity() {
    let data;
    try {
        data = await fetchJson("/api/security");
    } catch (error) {
        console.error("Failed to update security data", error);
        data = {
            double_spend: { orphan_count: 0, orphans: [], risk_level: "unknown" },
            attack_51: { pools: [], top_pool_share: 0, risk_level: "unknown" },
            invalid_blocks: { invalid_count: 0, risk_level: "unknown" },
            reorgs: { reorg_count: 0, reorgs: [], max_branch_length: 0, risk_level: "unknown" },
        };
    }
    updateNetworkSecuritySummary(data);
    const now = new Date().toLocaleTimeString();
    document.getElementById("securityLastUpdate").textContent = `Updated: ${now}`;

    const ds = data.double_spend ?? {};
    document.getElementById("doubleSpendCount").textContent = ds.orphan_count ?? "0";
    const dsRisk = document.getElementById("doubleSpendRisk");
    dsRisk.textContent = riskLabel(ds.risk_level);
    dsRisk.className = `text-xs font-semibold mt-1 inline-block ${riskClass(ds.risk_level)}`;

    const a51 = data.attack_51 ?? {};
    document.getElementById("topPoolShare").textContent = a51.top_pool_share ? `${a51.top_pool_share}%` : "-";
    document.getElementById("topPoolName").textContent = a51.pools?.[0]?.name ?? "Unknown";
    const ar = document.getElementById("attackRisk");
    ar.textContent = riskLabel(a51.risk_level);
    ar.className = `text-xs font-semibold mt-1 inline-block ${riskClass(a51.risk_level)}`;

    const ib = data.invalid_blocks ?? {};
    document.getElementById("invalidBlockCount").textContent = ib.invalid_count ?? "0";
    const ibRisk = document.getElementById("invalidBlockRisk");
    ibRisk.textContent = riskLabel(ib.risk_level);
    ibRisk.className = `text-xs font-semibold mt-1 inline-block ${riskClass(ib.risk_level)}`;

    const rg = data.reorgs ?? {};
    document.getElementById("reorgCount").textContent = rg.reorg_count ?? "0";
    document.getElementById("reorgMaxBranch").textContent = `Max Branch Length: ${rg.max_branch_length ?? 0}`;
    const rgRisk = document.getElementById("reorgRisk");
    rgRisk.textContent = riskLabel(rg.risk_level);
    rgRisk.className = `text-xs font-semibold mt-1 inline-block ${riskClass(rg.risk_level)}`;

    const poolList = document.getElementById("poolList");
    poolList.innerHTML = (a51.pools || []).map(pool => {
        const barColor = pool.risk === "critical" ? "#dc2626" : pool.risk === "high" ? "#ef4444" : pool.risk === "medium" ? "#f59e0b" : "#22c55e";
        return `
            <div class="flex items-center gap-3">
                <span class="text-xs text-gray-300 w-32 truncate">${escapeHtml(pool.name)}</span>
                <div class="flex-1 bg-gray-700 rounded" style="height:8px">
                    <div class="pool-bar" style="width:${Math.min(pool.share, 100)}%;background:${barColor}"></div>
                </div>
                <span class="text-xs w-14 text-right ${riskClass(pool.risk)}">${pool.share}%</span>
                <span class="text-xs text-gray-500 w-16 text-right">${pool.blocks} blocks</span>
            </div>`;
    }).join("");

    const orphanList = document.getElementById("orphanList");
    if (!ds.orphans || ds.orphans.length === 0) {
        orphanList.innerHTML = `<p class="text-gray-500">No orphaned blocks detected.</p>`;
    } else {
        orphanList.innerHTML = ds.orphans.map(o => `
            <div class="rounded bg-gray-800 p-2">
                <span class="text-yellow-400">Block #${o.height}</span>
                <span class="ml-2 text-gray-500">${o.hash}</span>
                <span class="ml-2 text-gray-400">branch: ${o.branch_len}</span>
                <span class="ml-2 text-xs text-gray-500">${o.status}</span>
            </div>`).join("");
    }

    const reorgList = document.getElementById("reorgList");
    if (!rg.reorgs || rg.reorgs.length === 0) {
        reorgList.innerHTML = `<p class="text-gray-500">No reorg events detected.</p>`;
    } else {
        reorgList.innerHTML = rg.reorgs.map(r => `
            <div class="rounded bg-gray-800 p-2">
                <span class="${riskClass(r.severity)}">Block #${r.height}</span>
                <span class="ml-2 text-gray-500">${r.hash}</span>
                <span class="ml-2 text-gray-400">branch: ${r.branch_len}</span>
                <span class="ml-2 text-xs text-gray-500">depth: ${r.depth_from_tip}</span>
            </div>`).join("");
    }
}

async function fetchPrice() { return fetchJson("/api/price"); }
async function fetchBtcTrend(timeframe = "1d") { return fetchJson(`/api/btc-trend-zone?tf=${encodeURIComponent(timeframe)}`); }

function renderBtcPriceCard(data) {
    const latest = data.price_usd ?? data.latest;
    const btcPrice = document.getElementById("btcPrice");
    if (btcPrice) btcPrice.innerText = formatUsd(latest);

    const changeUsd = Number(data.change_24h_usd);
    const changePercent = Number(data.change_24h_percent);
    const priceChange = document.getElementById("btcPriceChange");
    if (priceChange) {
        const hasChange = Number.isFinite(changeUsd) && Number.isFinite(changePercent);
        priceChange.innerText = hasChange
            ? `${formatSignedUsd(changeUsd)} (${formatSignedPercent(changePercent)})`
            : "N/A";
        priceChange.className = !hasChange || changeUsd === 0
            ? "text-gray-500"
            : changeUsd > 0
            ? "text-green-400"
            : "text-red-400";
    }

    const timestamp = document.getElementById("btcPriceTimestamp");
    if (timestamp) timestamp.innerText = `Updated: ${data.updated_at ? formatDateTime(data.updated_at) : "N/A"}`;
}

function btcTrendZoneColor(zone) {
    return {
        green: "#22c55e",
        yellow: "#f59e0b",
        blue: "#38bdf8",
        red: "#ef4444",
    }[zone] || "#9ca3af";
}

function btcTrendZoneIcon(zone) {
    return {
        green: "🟢",
        yellow: "🟡",
        blue: "🔵",
        red: "🔴",
    }[zone] || "";
}

function btcTrendChartData(rows) {
    const labels = rows.map((row) => formatChartTimeLabel(row.time));
    const pointColors = rows.map((row) => btcTrendZoneColor(row.zone));
    return {
        labels,
        datasets: [
            {
                label: "BTC Close",
                data: rows.map((row) => row.close),
                borderColor: "#f8fafc",
                backgroundColor: "rgba(248,250,252,0.08)",
                pointBackgroundColor: pointColors,
                pointBorderColor: pointColors,
                pointRadius: 2,
                tension: 0.2,
                fill: true,
            },
            {
                label: "EMA 12",
                data: rows.map((row) => row.ema12),
                borderColor: "#22c55e",
                backgroundColor: "rgba(34,197,94,0.08)",
                pointRadius: 0,
                tension: 0.2,
            },
            {
                label: "EMA 26",
                data: rows.map((row) => row.ema26),
                borderColor: "#f59e0b",
                backgroundColor: "rgba(245,158,11,0.08)",
                pointRadius: 0,
                tension: 0.2,
            },
        ],
    };
}

function btcTrendChartOptions() {
    return {
        ...sharedChartOptions,
        scales: {
            ...sharedChartOptions.scales,
            y: {
                ...sharedChartOptions.scales.y,
                beginAtZero: false,
                ticks: {
                    ...sharedChartOptions.scales.y.ticks,
                    callback: (value) => formatCompactUsd(value),
                },
            },
        },
    };
}

function renderBtcTrendSummary(data) {
    const signalEl = document.getElementById("btcTrendSignal");
    const confidenceEl = document.getElementById("btcTrendConfidence");
    const timeframeEl = document.getElementById("btcTrendTimeframe");
    const fallbackEl = document.getElementById("btcTrendFallback");
    const zone = data.zone ?? "unknown";
    const signal = data.signal ?? "Unavailable";
    if (signalEl) {
        signalEl.textContent = `${btcTrendZoneIcon(zone)} ${signal}`.trim();
        signalEl.style.color = btcTrendZoneColor(zone);
    }
    if (confidenceEl) confidenceEl.textContent = `Confidence: ${Number(data.confidence) || 0}%`;
    if (timeframeEl) timeframeEl.textContent = `Timeframe: ${data.timeframe ?? btcTrendTimeframe.toUpperCase()}`;
    if (fallbackEl) {
        const unavailable = !Array.isArray(data.data) || data.data.length === 0 || data.status === "error";
        fallbackEl.textContent = data.error || "Trend data temporarily unavailable.";
        fallbackEl.classList.toggle("hidden", !unavailable);
    }
}

function setBtcTrendActiveTimeframe(timeframe) {
    document.querySelectorAll(".btc-trend-tf").forEach((button) => {
        const active = button.dataset.timeframe === timeframe;
        button.className = active
            ? "btc-trend-tf rounded border border-amber-400 bg-amber-400/10 px-3 py-1 text-xs font-semibold text-amber-200 transition hover:border-amber-400"
            : "btc-trend-tf rounded border border-gray-700 px-3 py-1 text-xs font-semibold text-gray-300 transition hover:border-amber-400";
    });
}

function initBtcTrendTimeframeButtons() {
    setBtcTrendActiveTimeframe(btcTrendTimeframe);
    document.querySelectorAll(".btc-trend-tf").forEach((button) => {
        if (button.dataset.bound === "true") return;
        button.dataset.bound = "true";
        button.addEventListener("click", async () => {
            btcTrendTimeframe = button.dataset.timeframe || "1d";
            setBtcTrendActiveTimeframe(btcTrendTimeframe);
            await updatePriceChart();
        });
    });
}

async function initPriceChart() {
    let data = { timeframe: "1D", signal: "Unavailable", zone: "unknown", confidence: 0, data: [] };
    try {
        data = await fetchBtcTrend(btcTrendTimeframe);
    } catch (error) {
        console.error("Failed to initialize BTC trend chart", error);
    }
    const trendRows = data.data ?? [];
    priceChart = new Chart(document.getElementById("priceChart"), {
        type: "line",
        data: btcTrendChartData(trendRows),
        options: btcTrendChartOptions(),
    });
    renderBtcTrendSummary(data);
    initBtcTrendTimeframeButtons();
}

async function updatePriceChart() {
    try {
        const data = await fetchBtcTrend(btcTrendTimeframe);
        priceChart.data = btcTrendChartData(data.data ?? []);
        renderBtcTrendSummary(data);
        priceChart.update();
    } catch (error) {
        console.error("Failed to update BTC trend", error);
        renderBtcTrendSummary({ timeframe: btcTrendTimeframe.toUpperCase(), signal: "Unavailable", zone: "unknown", confidence: 0, data: [], error: "Trend data temporarily unavailable." });
    }
}

async function updateBtcPriceCard() {
    try {
        renderBtcPriceCard(await fetchPrice());
    } catch (error) {
        console.error("Failed to update BTC price", error);
        const btcPrice = document.getElementById("btcPrice");
        if (btcPrice) btcPrice.innerText = "N/A";
    }
}

// ── Fee ───────────────────────────────────────────────────
async function fetchFee() { return fetchJson("/api/fees"); }

async function initFeeChart() {
    let data = { height: [], fee: [] };
    try {
        data = await fetchFee();
    } catch (error) {
        console.error("Failed to initialize fee chart", error);
    }
    feeChart = new Chart(document.getElementById("feeChart"), {
        type: "line",
        data: { labels: data.height ?? [], datasets: [{ label: "sat/vB", data: data.fee ?? [], borderColor: "#3b82f6", backgroundColor: "rgba(59,130,246,0.12)", tension: 0.25, fill: true }] },
        options: sharedChartOptions,
    });
}

async function updateFeeChart() {
    try {
        const data = await fetchFee();
        if (!feeChart) return;
        feeChart.data.labels = data.height ?? [];
        feeChart.data.datasets[0].data = data.fee ?? [];
        feeChart.update();
    } catch (error) {
        console.error("Failed to update fee chart", error);
    }
}

// ── Transactions ──────────────────────────────────────────
async function fetchTransactions() { return fetchJson("/api/transactions"); }

async function initTxChart() {
    let data = { height: [], tx_count: [] };
    try {
        data = await fetchTransactions();
    } catch (error) {
        console.error("Failed to initialize transaction chart", error);
    }
    txChart = new Chart(document.getElementById("txChart"), {
        type: "bar",
        data: { labels: data.height ?? [], datasets: [{ label: "Transactions", data: data.tx_count ?? [], borderColor: "#a855f7", backgroundColor: "rgba(168,85,247,0.45)", borderWidth: 1 }] },
        options: sharedChartOptions,
    });
}

async function updateTxChart() {
    try {
        const data = await fetchTransactions();
        if (!txChart) return;
        txChart.data.labels = data.height ?? [];
        txChart.data.datasets[0].data = data.tx_count ?? [];
        txChart.update();
    } catch (error) {
        console.error("Failed to update transaction chart", error);
    }
}

// ── Hashrate ──────────────────────────────────────────────
async function fetchHash() { return fetchJson("/api/hashrate"); }

async function initHashChart() {
    let data = { time: [], hashrate: [] };
    try {
        data = await fetchHash();
    } catch (error) {
        console.error("Failed to initialize hashrate chart", error);
    }
    const labels = (data.time ?? []).map(formatChartTimeLabel);
    hashChart = new Chart(document.getElementById("hashChart"), {
        type: "line",
        data: { labels, datasets: [{ label: "Hashrate", data: data.hashrate ?? [], borderColor: "#f59e0b", backgroundColor: "rgba(245,158,11,0.12)", tension: 0.25, fill: true }] },
        options: sharedChartOptions,
    });
}

async function updateHashChart() {
    try {
        const data = await fetchHash();
        if (!hashChart) return;
        hashChart.data.labels = (data.time ?? []).map(formatChartTimeLabel);
        hashChart.data.datasets[0].data = data.hashrate ?? [];
        hashChart.update();
        document.getElementById("hashrate").innerText = valueOrNA(data.latest);
    } catch (error) {
        console.error("Failed to update hashrate chart", error);
    }
}

// ── Network ───────────────────────────────────────────────
function renderNetworkMetrics(data) {
    const hashrateEl = document.getElementById("hashrate");
    const nodesEl = document.getElementById("nodes");
    const marketCapEl = document.getElementById("btcMarketCap");
    const ageEl = document.getElementById("bitcoinAgeDays");
    const halvingDaysEl = document.getElementById("nextHalvingDays");
    const blocksLeftEl = document.getElementById("halvingBlocksLeft");

    if (hashrateEl) hashrateEl.innerText = valueOrNA(data.hashrate);
    if (nodesEl) nodesEl.innerText = valueOrNA(data.nodes);
    if (marketCapEl) marketCapEl.innerText = formatCompactUsd(data.market_cap_usd);
    if (ageEl) {
        ageEl.innerText = data.bitcoin_age_days === null || data.bitcoin_age_days === undefined
            ? "N/A"
            : `${formatInteger(data.bitcoin_age_days)} Days`;
    }
    if (halvingDaysEl) {
        halvingDaysEl.innerText = data.halving_eta_days === null || data.halving_eta_days === undefined
            ? "N/A"
            : `${formatInteger(data.halving_eta_days)} Days`;
    }
    if (blocksLeftEl) {
        blocksLeftEl.innerText = data.blocks_remaining === null || data.blocks_remaining === undefined
            ? "N/A"
            : formatInteger(data.blocks_remaining);
    }
}

async function updateNetwork() {
    try {
        renderNetworkMetrics(await fetchJson("/api/network"));
    } catch (error) {
        console.error("Failed to update network data", error);
        renderNetworkMetrics({ hashrate: "N/A", nodes: "N/A" });
    }
}

async function updateNetworkNodes() {
    try {
        renderNetworkMetrics(await fetchJson("/api/network"));
    } catch (error) {
        console.error("Failed to update node count", error);
        const nodesEl = document.getElementById("nodes");
        if (nodesEl) nodesEl.innerText = "N/A";
    }
}

async function updateViewers() {
    try {
        const data = await fetchJson("/api/viewers");
        document.getElementById("viewerTotal").innerText = valueOrNA(data.total_views);
        document.getElementById("viewerUnique").innerText = valueOrNA(data.unique_visitors);
        document.getElementById("viewerLastSeen").innerText = formatDateTime(data.last_viewed_at);
    } catch (error) {
        console.error("Failed to update viewer stats", error);
        document.getElementById("viewerTotal").innerText = "N/A";
        document.getElementById("viewerUnique").innerText = "N/A";
        document.getElementById("viewerLastSeen").innerText = "N/A";
    }
}

// Institutional metrics
async function fetchEtfFlow() { return fetchJson("/api/etf"); }
async function fetchTreasury() { return fetchJson("/api/treasury"); }
async function fetchFearGreed() { return fetchJson("/api/fear-greed"); }
async function fetchSupplyOwnership() { return fetchJson("/api/ownership"); }
async function fetchMvrvSummary() { return fetchJson("/api/mvrv"); }
async function fetchMvrvHistory() { return fetchJson("/api/mvrv/history"); }

function trackDashboardEvent(eventName) {
    fetch("/api/analytics/event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event: eventName }),
        keepalive: true,
    }).catch((error) => console.debug("Analytics event skipped", eventName, error));
}

function initAccordionPanels() {
    document.querySelectorAll("[data-accordion-toggle]").forEach((toggle) => {
        const panelId = toggle.getAttribute("aria-controls");
        const panel = document.getElementById(panelId || "");
        if (!panel) return;
        toggle.addEventListener("click", () => {
            const expanded = toggle.getAttribute("aria-expanded") === "true";
            const nextExpanded = !expanded;
            toggle.setAttribute("aria-expanded", String(nextExpanded));
            panel.hidden = !nextExpanded;
            toggle.textContent = nextExpanded
                ? toggle.dataset.openText || "▲ Hide"
                : toggle.dataset.closedText || "▼ Show";
            const eventName = nextExpanded ? toggle.dataset.openEvent : toggle.dataset.closeEvent;
            if (eventName) trackDashboardEvent(eventName);
            if (nextExpanded) {
                if (panelId === "etfHistoryPanel") loadEtfFlowChartOnce();
                if (panelId === "ownershipDetailsPanel") loadSupplyOwnershipOnce();
                if (panelId === "advancedNetworkPanel") updateSecurity();
            }
        });
    });
}

function mvrvZoneClass(zone) {
    if (zone === "Deep Value") return "rounded bg-green-800 px-2 py-1 text-xs font-semibold text-green-100";
    if (zone === "Accumulation") return "rounded bg-emerald-800 px-2 py-1 text-xs font-semibold text-emerald-100";
    if (zone === "Neutral / Warm") return "rounded bg-amber-700 px-2 py-1 text-xs font-semibold text-amber-100";
    if (zone === "Overheated") return "rounded bg-red-800 px-2 py-1 text-xs font-semibold text-red-100";
    return "rounded bg-gray-800 px-2 py-1 text-xs font-semibold text-gray-300";
}

function renderMvrvSummary(data) {
    const value = Number(data?.value);
    const valueEl = document.getElementById("mvrvValue");
    const zoneEl = document.getElementById("mvrvZone");
    const descriptionEl = document.getElementById("mvrvDescription");
    const sourceEl = document.getElementById("mvrvSource");
    const updatedEl = document.getElementById("mvrvUpdated");

    if (valueEl) {
        valueEl.textContent = Number.isFinite(value) ? value.toFixed(2) : "N/A";
    }
    if (zoneEl) {
        zoneEl.textContent = valueOrNA(data?.zone);
        zoneEl.className = mvrvZoneClass(data?.zone);
    }
    if (descriptionEl) descriptionEl.textContent = data?.description || "MVRV data is temporarily unavailable.";
    if (sourceEl) sourceEl.textContent = `Source: ${valueOrNA(data?.source)}`;
    if (updatedEl) updatedEl.textContent = `Updated: ${formatDateTime(data?.updated_at)}`;
}

async function updateMvrvSummary() {
    try {
        renderMvrvSummary(await fetchMvrvSummary());
    } catch (error) {
        console.error("Failed to update MVRV summary", error);
        renderMvrvSummary({
            value: "N/A",
            zone: "N/A",
            description: "MVRV data is temporarily unavailable.",
            source: "CoinMetrics",
            updated_at: null,
        });
    }
}

function renderMvrvChart(data) {
    const rows = (data?.data ?? []).filter((row) => Number.isFinite(Number(row.mvrv)));
    const statusEl = document.getElementById("mvrvChartStatus");
    if (!rows.length) {
        if (statusEl) {
            statusEl.textContent = "MVRV chart is temporarily unavailable.";
            statusEl.classList.remove("hidden");
        }
        return;
    }
    if (statusEl) {
        statusEl.textContent = `Source: ${valueOrNA(data?.source)}`;
        statusEl.classList.remove("hidden");
    }
    const labels = rows.map((row) => formatFlowDate(row.date));
    const values = rows.map((row) => Number(row.mvrv));
    const chartEl = document.getElementById("mvrvChart");
    if (!chartEl) return;
    if (mvrvChart) {
        mvrvChart.data.labels = labels;
        mvrvChart.data.datasets[0].data = values;
        mvrvChart.update();
        return;
    }
    mvrvChart = new Chart(chartEl, {
        type: "line",
        data: {
            labels,
            datasets: [{
                label: "MVRV Ratio",
                data: values,
                borderColor: "#f59e0b",
                backgroundColor: "rgba(245,158,11,0.12)",
                tension: 0.25,
                fill: true,
                pointRadius: 0,
            }],
        },
        options: sharedChartOptions,
    });
}

async function loadMvrvHistoryOnce() {
    if (mvrvHistoryLoaded || mvrvHistoryLoading) return;
    mvrvHistoryLoading = true;
    const statusEl = document.getElementById("mvrvChartStatus");
    if (statusEl) {
        statusEl.textContent = "Loading historical MVRV...";
        statusEl.classList.remove("hidden");
    }
    try {
        renderMvrvChart(await fetchMvrvHistory());
        mvrvHistoryLoaded = true;
    } catch (error) {
        console.error("Failed to load MVRV history", error);
        if (statusEl) {
            statusEl.textContent = "MVRV chart is temporarily unavailable.";
            statusEl.classList.remove("hidden");
        }
    } finally {
        mvrvHistoryLoading = false;
    }
}

function initMvrvSection() {
    const toggle = document.getElementById("mvrvChartToggle");
    const panel = document.getElementById("mvrvChartPanel");
    if (!toggle || !panel) return;
    toggle.addEventListener("click", () => {
        const isExpanded = toggle.getAttribute("aria-expanded") === "true";
        if (isExpanded) {
            panel.classList.add("hidden");
            toggle.setAttribute("aria-expanded", "false");
            toggle.textContent = "▼ Show Historical Chart";
            trackDashboardEvent("mvrv_chart_close");
            return;
        }
        panel.classList.remove("hidden");
        toggle.setAttribute("aria-expanded", "true");
        toggle.textContent = "▲ Hide Historical Chart";
        trackDashboardEvent("mvrv_chart_open");
        loadMvrvHistoryOnce();
    });
}

function fearGreedClass(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "text-gray-300";
    if (numeric <= 24) return "text-red-400";
    if (numeric <= 49) return "text-amber-300";
    if (numeric <= 74) return "text-green-300";
    return "text-green-400";
}

function fearGreedChip(row) {
    const value = valueOrNA(row?.value);
    const label = valueOrNA(row?.classification);
    return value === "N/A" ? "N/A" : `${value} ${label}`;
}

function renderFearGreed(data) {
    const value = valueOrNA(data?.value);
    const classification = valueOrNA(data?.classification);
    const source = data?.source_label ?? data?.source ?? "Alternative.me";
    const timestamp = data?.data_timestamp ? ` | Data: ${formatDateTime(data.data_timestamp)}` : "";
    const status = data?.status === "stale" ? "Stale" : data?.status === "ok" ? "Daily" : "N/A";
    const historical = data?.historical ?? {};
    const valueEl = document.getElementById("fearGreedValue");
    const labelEl = document.getElementById("fearGreedLabel");
    const sourceEl = document.getElementById("fearGreedSource");
    const statusEl = document.getElementById("fearGreedStatus");
    const markerEl = document.getElementById("fearGreedMarker");
    const yesterdayEl = document.getElementById("fearGreedYesterday");
    const weekEl = document.getElementById("fearGreedWeek");
    const monthEl = document.getElementById("fearGreedMonth");
    if (valueEl) {
        valueEl.textContent = value;
        valueEl.className = `text-2xl font-bold ${fearGreedClass(value)}`;
    }
    if (labelEl) labelEl.textContent = classification;
    if (sourceEl) sourceEl.textContent = `Source: ${source}${timestamp}`;
    if (markerEl) {
        const numeric = Number(value);
        const pct = Number.isFinite(numeric) ? Math.min(100, Math.max(0, numeric)) : 50;
        markerEl.style.marginLeft = `calc(${pct}% - 2px)`;
    }
    if (yesterdayEl) yesterdayEl.textContent = fearGreedChip(historical.yesterday);
    if (weekEl) weekEl.textContent = fearGreedChip(historical.last_week);
    if (monthEl) monthEl.textContent = fearGreedChip(historical.last_month);
    if (statusEl) {
        statusEl.textContent = status;
        statusEl.className = data?.status === "ok"
            ? "rounded bg-amber-700/70 px-2 py-1 text-xs font-semibold text-amber-100"
            : data?.status === "stale"
            ? "rounded bg-gray-700 px-2 py-1 text-xs font-semibold text-gray-200"
            : "rounded bg-gray-800 px-2 py-1 text-xs font-semibold text-gray-300";
    }
}

async function updateFearGreed() {
    try {
        renderFearGreed(await fetchFearGreed());
    } catch (error) {
        console.error("Failed to update Fear & Greed data", error);
        renderFearGreed({ value: "N/A", classification: "N/A", source_label: "Alternative.me", status: "error" });
    }
}

function institutionalInsight(etfData, treasuryData, priceData) {
    const prices = priceData?.history ?? [];
    const latestPrice = Number(prices.at(-1));
    const previousPrice = Number(prices.at(-2));
    const priceRising = Number.isFinite(latestPrice) && Number.isFinite(previousPrice) && latestPrice > previousPrice;
    const priceFalling = Number.isFinite(latestPrice) && Number.isFinite(previousPrice) && latestPrice < previousPrice;
    const dominance = Number(treasuryData?.treasury_dominance_percent);
    const etfTrend = etfData?.trend ?? etfData?.status;

    if (etfTrend === "inflow" && priceRising) {
        return "ETF inflow with rising BTC price: institutional demand is positive.";
    }
    if (etfTrend === "outflow" && priceFalling) {
        return "ETF outflow with falling BTC price: institutional posture is risk-off.";
    }
    if (etfTrend === "inflow") {
        return "ETF inflow is visible; watch whether BTC price confirms the demand signal.";
    }
    if (etfTrend === "outflow") {
        return "ETF outflow is visible; institutional demand is cooling until flows stabilize.";
    }
    if (Number.isFinite(dominance) && dominance > 0) {
        return "Treasury dominance is visible: long-term accumulation remains an institutional signal.";
    }
    return "Institutional signal is neutral until fresh ETF or treasury data is available.";
}

function etfNumericHistory(etfData) {
    return (etfData?.flow_history ?? [])
        .map((row) => Number(row?.net_flow_usd))
        .filter((value) => Number.isFinite(value));
}

function etfFlowSum(values, days) {
    if (!values.length) return null;
    return values.slice(-days).reduce((total, value) => total + value, 0);
}

function etfFlowStreak(values) {
    if (!values.length) return { label: "⚪ No Streak", direction: "neutral" };
    const latest = values.at(-1);
    if (latest === 0) return { label: "⚪ No Streak", direction: "neutral" };

    const direction = latest > 0 ? "inflow" : "outflow";
    let count = 0;
    for (let index = values.length - 1; index >= 0; index -= 1) {
        if ((direction === "inflow" && values[index] <= 0) || (direction === "outflow" && values[index] >= 0)) break;
        count += 1;
    }

    const marker = direction === "inflow" ? "🟢" : "🔴";
    const label = direction === "inflow" ? "Inflow" : "Outflow";
    return { label: `${marker} ${count} Days ${label}`, direction };
}

function setEtfTrendValue(id, value, direction = null) {
    const element = document.getElementById(id);
    if (!element) return;

    const numeric = Number(value);
    const hasValue = Number.isFinite(numeric);
    const colorDirection = direction ?? (hasValue && numeric > 0 ? "inflow" : hasValue && numeric < 0 ? "outflow" : "neutral");
    element.textContent = hasValue ? formatSignedCompactUsd(numeric) : "N/A";
    element.className = colorDirection === "inflow"
        ? "block font-semibold text-green-400"
        : colorDirection === "outflow"
        ? "block font-semibold text-red-400"
        : "block font-semibold text-gray-500";
}

function renderEtfTrendSummary(etfData) {
    const values = etfNumericHistory(etfData);
    setEtfTrendValue("etfFlow7d", etfFlowSum(values, 7));
    setEtfTrendValue("etfFlow30d", etfFlowSum(values, 30));

    const streak = etfFlowStreak(values);
    const streakEl = document.getElementById("etfFlowStreak");
    if (!streakEl) return;
    streakEl.textContent = values.length ? streak.label : "N/A";
    streakEl.className = streak.direction === "inflow"
        ? "block font-semibold text-green-400"
        : streak.direction === "outflow"
        ? "block font-semibold text-red-400"
        : "block font-semibold text-gray-500";
}

function renderInstitutionalCards(etfData, treasuryData, priceData) {
    document.getElementById("etfNetFlow").innerText = formatCompactUsd(etfData.latest_net_flow_usd);
    const etfSource = etfData.source_label ?? etfData.source ?? "fallback";
    const latestDate = etfData.latest_date ? ` | Latest: ${etfData.latest_date}` : "";
    document.getElementById("etfFlowSource").innerText = `${formatMinutesAgo(etfData.updated_at)} | Source: ${etfSource}${latestDate}`;
    renderEtfTrendSummary(etfData);

    const status = etfData.trend ?? etfData.status ?? "neutral";
    const statusEl = document.getElementById("etfFlowStatus");
    statusEl.innerText = status.toUpperCase();
    statusEl.className = status === "inflow"
        ? "rounded bg-green-700 px-2 py-1 text-xs font-semibold text-green-100"
        : status === "outflow"
        ? "rounded bg-red-700 px-2 py-1 text-xs font-semibold text-red-100"
        : "rounded bg-gray-800 px-2 py-1 text-xs font-semibold text-gray-300";

    document.getElementById("treasuryBtcHeld").innerText = formatBtc(valueOrNA(treasuryData.total_btc_held));
    document.getElementById("treasuryDominance").innerText = `Dominance: ${formatPercent(valueOrNA(treasuryData.treasury_dominance_percent))}`;
    const treasurySource = treasuryData.source_label ?? treasuryData.source ?? "unknown";
    const treasuryChecked = treasuryData.updated_at ? ` | Last checked: ${formatDateTime(treasuryData.updated_at)}` : "";
    const treasurySourceEl = document.getElementById("treasurySource");
    treasurySourceEl.innerText = `Source: ${treasurySource}`;
    treasurySourceEl.title = `Source: ${treasurySource}${treasuryChecked}`;
    document.getElementById("institutionalInsight").innerText = institutionalInsight(etfData, treasuryData, priceData);

    const holders = treasuryData.top_holders ?? [];
    document.getElementById("treasuryTopHolders").innerHTML = holders.length
        ? holders.map((holder) => `
            <div class="flex justify-between gap-3">
                <span class="truncate">${escapeHtml(holder.name ?? "Unknown")}</span>
                <span class="shrink-0 text-gray-300">${formatBtc(holder.btc_held)}</span>
            </div>`).join("")
        : `<p class="text-gray-500">Top holders unavailable.</p>`;
}

function renderEtfFlowNote(etfData) {
    const note = document.getElementById("etfFlowNote");
    if (!note) return;
    const history = etfData.flow_history ?? [];
    if (etfData.is_fallback || etfData.is_stale) {
        note.textContent = etfData.data_note || "ETF flow history is using fallback estimate data. Live data unavailable.";
        note.classList.remove("hidden");
        return;
    }
    if (!history.length) {
        note.textContent = "ETF flow history unavailable.";
        note.classList.remove("hidden");
        return;
    }
    note.textContent = "";
    note.classList.add("hidden");
}

function etfChartRows(etfData) {
    return (etfData.flow_history ?? []).filter((row) => Number.isFinite(Number(row.net_flow_usd)));
}

async function initEtfFlowChart() {
    let etfData = { flow_history: [], latest_net_flow_usd: 0, trend: "neutral", source: "fallback", updated_at: null };
    let treasuryData = { total_btc_held: 0, treasury_dominance_percent: 0, top_holders: [] };
    let priceData = { history: [] };
    try {
        [etfData, treasuryData, priceData] = await Promise.all([fetchEtfFlow(), fetchTreasury(), fetchPrice()]);
    } catch (error) {
        console.error("Failed to initialize institutional metrics", error);
    }
    const history = etfChartRows(etfData);
    etfFlowChart = new Chart(document.getElementById("etfFlowChart"), {
        type: "bar",
        data: {
            labels: history.map((row) => formatFlowDate(row.date)),
            datasets: [{
                label: "Net Flow USD",
                data: history.map((row) => row.net_flow_usd === "N/A" ? 0 : row.net_flow_usd),
                backgroundColor: history.map((row) => Number(row.net_flow_usd) < 0 ? "rgba(239,68,68,0.55)" : "rgba(34,197,94,0.55)"),
                borderColor: history.map((row) => Number(row.net_flow_usd) < 0 ? "#ef4444" : "#22c55e"),
                borderWidth: 1,
            }],
        },
        options: sharedChartOptions,
    });
    renderEtfFlowNote(etfData);
    renderInstitutionalCards(etfData, treasuryData, priceData);
    etfFlowChartLoaded = true;
}

async function initInstitutionalSummary() {
    let etfData = { flow_history: [], latest_net_flow_usd: 0, trend: "neutral", source: "fallback", updated_at: null };
    let treasuryData = { total_btc_held: 0, treasury_dominance_percent: 0, top_holders: [] };
    let priceData = { history: [] };
    try {
        [etfData, treasuryData, priceData] = await Promise.all([fetchEtfFlow(), fetchTreasury(), fetchPrice()]);
    } catch (error) {
        console.error("Failed to initialize institutional summary", error);
    }
    renderInstitutionalCards(etfData, treasuryData, priceData);
}

async function loadEtfFlowChartOnce() {
    if (etfFlowChartLoaded || etfFlowChartLoading) return;
    etfFlowChartLoading = true;
    try {
        await initEtfFlowChart();
    } finally {
        etfFlowChartLoading = false;
    }
}

function renderSupplyOwnership(data) {
    const categories = data.categories ?? data.ownership ?? [];
    document.getElementById("supplyOwnershipNote").innerText = data.note ?? "Estimated ownership distribution.";
    document.getElementById("supplyCirculating").innerText = `Circulating: ${formatBtc(data.circulating_supply ?? data.circulating_supply_btc)}`;
    document.getElementById("supplyRemaining").innerText = formatBtc(data.remaining_to_mine);
    document.getElementById("supplyPercentMined").innerText = formatPercent(data.percent_mined);
    const lost = data.estimated_lost_btc ?? {};
    document.getElementById("supplyLostEstimate").innerText = `${formatBtc(lost.low)} - ${formatBtc(lost.high)}`;
    const liquid = data.effective_liquid_supply ?? {};
    document.getElementById("supplyLiquidEstimate").innerText = `${formatBtc(liquid.low)} - ${formatBtc(liquid.high)}`;
    document.getElementById("supplyOwnershipUpdated").innerText = `Updated: ${formatDateTime(data.updated_at)}`;
    document.getElementById("supplyInsightCards").innerHTML = (data.insights ?? []).map((insight) => `
        <div class="rounded border border-amber-500/20 bg-gray-950 p-3 text-sm text-gray-200">
            ${escapeHtml(insight)}
        </div>`).join("");
    document.getElementById("supplyOwnershipList").innerHTML = categories.length
        ? categories.map((row) => `
            <div class="rounded-lg border border-gray-800 bg-gray-950 p-3">
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <span class="font-semibold text-gray-100">${escapeHtml(row.name ?? row.label)}</span>
                    <span class="text-gray-300">${formatPercent(row.percent)}</span>
                </div>
                <div class="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-400 sm:grid-cols-4">
                    <span>${formatOwnershipBtc(row)}</span>
                    <span>${escapeHtml(row.source_type ?? row.source ?? "Estimating")}</span>
                    <span class="${confidenceClass(row.confidence_level ?? row.confidence)}">${escapeHtml(row.confidence ?? "approximate")}</span>
                    <span>${escapeHtml(row.status_label ?? (row.estimated ? "Estimated" : "Live"))}</span>
                </div>
            </div>`).join("")
        : `<p class="text-gray-500">Ownership data unavailable.</p>`;
}

function formatOwnershipBtc(row) {
    if (row.display_btc) return row.display_btc;
    if (row.btc !== undefined && row.btc !== null) return formatBtc(row.btc);
    if (row.btc_range) return `${formatBtc(row.btc_range.low)} - ${formatBtc(row.btc_range.high)}`;
    return "Limited visibility";
}

function confidenceClass(confidence) {
    if (confidence === "high" || confidence === "verified/public filings") return "text-green-400";
    if (confidence === "medium" || confidence === "research estimate") return "text-amber-300";
    return "text-gray-500";
}

async function initSupplyOwnershipChart() {
    let data = { categories: [], note: "Loading...", circulating_supply: 0 };
    try {
        data = await fetchSupplyOwnership();
    } catch (error) {
        console.error("Failed to initialize BTC supply ownership", error);
    }
    const ownership = (data.chart_categories ?? data.categories ?? data.ownership ?? []).filter((row) => Number.isFinite(Number(row.btc)));
    supplyOwnershipChart = new Chart(document.getElementById("supplyOwnershipChart"), {
        type: "doughnut",
        data: {
            labels: ownership.map((row) => row.name ?? row.label),
            datasets: [{
                data: ownership.map((row) => row.btc),
                backgroundColor: ["#f59e0b", "#38bdf8", "#22c55e", "#a78bfa", "#fb7185", "#14b8a6", "#64748b"],
                borderColor: "#111827",
                borderWidth: 3,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: "58%",
            plugins: { legend: { position: "bottom", labels: { color: "#d1d5db", boxWidth: 12 } } },
        },
    });
    renderSupplyOwnership(data);
    supplyOwnershipLoaded = true;
}

async function loadSupplyOwnershipOnce() {
    if (supplyOwnershipLoaded || supplyOwnershipLoading) return;
    supplyOwnershipLoading = true;
    try {
        await initSupplyOwnershipChart();
    } finally {
        supplyOwnershipLoading = false;
    }
}

async function updateInstitutionalMetrics() {
    try {
        const [etfData, treasuryData, priceData] = await Promise.all([fetchEtfFlow(), fetchTreasury(), fetchPrice()]);
        const history = etfChartRows(etfData);
        if (etfFlowChart) {
            etfFlowChart.data.labels = history.map((row) => formatFlowDate(row.date));
            etfFlowChart.data.datasets[0].data = history.map((row) => row.net_flow_usd === "N/A" ? 0 : row.net_flow_usd);
            etfFlowChart.data.datasets[0].backgroundColor = history.map((row) => Number(row.net_flow_usd) < 0 ? "rgba(239,68,68,0.55)" : "rgba(34,197,94,0.55)");
            etfFlowChart.data.datasets[0].borderColor = history.map((row) => Number(row.net_flow_usd) < 0 ? "#ef4444" : "#22c55e");
            etfFlowChart.update();
        }
        renderEtfFlowNote(etfData);
        renderInstitutionalCards(etfData, treasuryData, priceData);
    } catch (error) {
        console.error("Failed to update institutional metrics", error);
        renderEtfFlowNote({ flow_history: [], is_fallback: true, is_stale: true });
        renderInstitutionalCards(
            { flow_history: [], latest_net_flow_usd: 0, trend: "neutral", source: "fallback", updated_at: null },
            { total_btc_held: 0, treasury_dominance_percent: 0, top_holders: [] },
            { history: [] },
        );
    }
}

async function updateSupplyOwnership() {
    if (!supplyOwnershipLoaded) return;
    try {
        const data = await fetchSupplyOwnership();
        const ownership = (data.chart_categories ?? data.categories ?? data.ownership ?? []).filter((row) => Number.isFinite(Number(row.btc)));
        if (supplyOwnershipChart) {
            supplyOwnershipChart.data.labels = ownership.map((row) => row.name ?? row.label);
            supplyOwnershipChart.data.datasets[0].data = ownership.map((row) => row.btc);
            supplyOwnershipChart.update();
        }
        renderSupplyOwnership(data);
    } catch (error) {
        console.error("Failed to update BTC supply ownership", error);
    }
}

// ── Alerts ────────────────────────────────────────────────
async function updateAlert() {
    let data;
    try {
        data = await fetchJson("/api/alert");
    } catch (error) {
        console.error("Failed to update alerts", error);
        data = { alerts: [] };
    }
    const alertBox = document.getElementById("alertBox");
    const alertCount = document.getElementById("alertCount");
    const alerts = data.alerts ?? [];
    const recentAlerts = data.recent_alerts ?? [];
    const recentAlertBox = document.getElementById("recentAlertBox");
    if (recentAlertBox) {
        recentAlertBox.innerHTML = recentAlerts.length === 0
            ? `<p class="text-xs text-gray-500">No recent alerts.</p>`
            : recentAlerts.map((alert) => {
                const status = alert.status ?? "yellow";
                const borderColor = status === "red"
                    ? "border-red-500/50 bg-red-950/25 text-red-100"
                    : status === "green"
                    ? "border-green-500/50 bg-green-950/25 text-green-100"
                    : "border-amber-500/50 bg-amber-950/25 text-amber-100";
                const time = alert.recorded_at ? formatDateTime(alert.recorded_at) : "";
                return `<article class="rounded border ${borderColor} p-2">
                    <div class="mb-1 flex items-center justify-between gap-3">
                        <span class="text-xs font-semibold uppercase text-gray-300">${escapeHtml((alert.type ?? "").replaceAll("_"," "))}</span>
                        <span class="text-xs text-gray-500">${escapeHtml(time)}</span>
                    </div>
                    <div class="text-sm font-semibold">${escapeHtml(alert.message ?? "")}</div>
                </article>`;
            }).join("");
    }
    alertCount.innerText = `${alerts.length} active`;
    if (alerts.length === 0) {
        alertBox.innerHTML = `<p class="text-sm text-gray-400">No active alerts.</p>`;
        return;
    }
    alertBox.innerHTML = alerts.map((alert) => {
        const status = alert.status ?? "yellow";
        const borderColor = status === "red"
            ? "border-red-500/70 bg-red-950/40 text-red-100"
            : status === "green"
            ? "border-green-500/70 bg-green-950/40 text-green-100"
            : "border-amber-500/70 bg-amber-950/40 text-amber-100";
        const icon = status === "red" ? "🔴" : status === "green" ? "✅" : "⚠️";
        const action = alert.action
            ? `<div class="mt-2 text-xs px-2 py-1 rounded bg-black/20">${icon} ${escapeHtml(alert.action)}</div>`
            : "";
        return `<article class="rounded border ${borderColor} p-3">
            <div class="mb-1 text-xs font-semibold uppercase text-gray-300">${escapeHtml((alert.type ?? "").replaceAll("_"," "))}</div>
            <div class="font-semibold">${escapeHtml(alert.message ?? "")}</div>
            ${action}
        </article>`;
    }).join("");
}


// ── Fee Recommendation ────────────────────────────────────
function feeBadgeClass(label) {
    const map = {
        very_low: "bg-green-700 text-green-100",
        low: "bg-green-600 text-green-100",
        medium: "bg-yellow-600 text-yellow-100",
        high: "bg-orange-600 text-orange-100",
        very_high: "bg-red-700 text-red-100",
    };
    return map[label] || "bg-gray-700 text-gray-300";
}

function feeBadgeText(label) {
    const map = {
        very_low: "Very Low",
        low: "Low",
        medium: "Normal",
        high: "High",
        very_high: "Very High",
    };
    return map[label] || label;
}

async function updateFeeRecommendation() {
    let data;
    try {
        data = await fetchJson("/api/fees");
    } catch (error) {
        console.error("Failed to update fee recommendation", error);
        data = {
            fastestFee: "N/A",
            halfHourFee: "N/A",
            hourFee: "N/A",
            status: "error",
            source: "mempool.space",
        };
    }

    document.getElementById("feeRecSource").textContent = `Source: ${data.source ?? "mempool.space"}`;

    const slots = [
        { key: "fastestFee", elFee: "feeNextBlock", elBadge: "feeNextBlockBadge", elAdvice: "feeNextBlockAdvice" },
        { key: "halfHourFee", elFee: "fee30Min", elBadge: "fee30MinBadge", elAdvice: "fee30MinAdvice" },
        { key: "hourFee", elFee: "fee1Hour", elBadge: "fee1HourBadge", elAdvice: "fee1HourAdvice" },
    ];

    for (const slot of slots) {
        const fee = valueOrNA(data[slot.key]);
        const label = fee === "N/A" ? "N/A" : (
            fee <= 2 ? "very_low" :
            fee <= 5 ? "low" :
            fee <= 15 ? "medium" :
            fee <= 30 ? "high" :
            "very_high"
        );
        const advice = fee === "N/A" ? "Data unavailable" : (
            fee <= 2 ? "Excellent - fees extremely low" :
            fee <= 5 ? "Good - cheap to send now" :
            fee <= 15 ? "Normal - reasonable fee" :
            fee <= 30 ? "Elevated - consider waiting" :
            "Very high - wait if possible"
        );
        document.getElementById(slot.elFee).textContent = fee;
        const badge = document.getElementById(slot.elBadge);
        badge.textContent = feeBadgeText(label);
        badge.className = `inline-block rounded px-2 py-1 text-xs font-semibold ${feeBadgeClass(label)}`;
        document.getElementById(slot.elAdvice).textContent = advice;
    }
}

// ── Refresh ───────────────────────────────────────────────
async function refreshBtcPriceCard() {
    await updateBtcPriceCard();
}

async function refreshPriceChart() {
    await updatePriceChart();
}

async function refreshMempoolMetrics() {
    await Promise.allSettled([
        updateFeeChart(),
        updateTxChart(),
        updateViewers(),
        updateAlert(),
        updateFeeRecommendation(),
    ]);
}

async function refreshHashrateMetrics() {
    await updateHashChart();
}

async function refreshNodeMetrics() {
    await Promise.allSettled([
        updateNetworkNodes(),
        updateSecurity(),
    ]);
}

async function refreshInstitutionalMetrics() {
    await Promise.allSettled([
        updateInstitutionalMetrics(),
        updateSupplyOwnership(),
    ]);
}

async function refreshFearGreed() {
    await updateFearGreed();
}

function startRefreshJob(name, task, intervalMs) {
    if (refreshJobs.has(name)) {
        console.debug(`refresh skipped ${name}: already scheduled`);
        return;
    }
    let running = false;
    const run = async () => {
        if (running) {
            console.debug(`refresh skipped ${name}: previous run still active`);
            return;
        }
        running = true;
        try {
            await task();
        } finally {
            running = false;
        }
    };
    refreshJobs.set(name, window.setInterval(run, intervalMs));
}

async function initDashboard() {
    await Promise.allSettled([
        initPriceChart(),
        initFeeChart(),
        initTxChart(),
        initHashChart(),
        initInstitutionalSummary(),
        updateNetwork(),
        updateViewers(),
        updateAlert(),
        updateSecurity(),
        updateFeeRecommendation(),
        updateFearGreed(),
        updateMvrvSummary(),
        initDonationBox(),
        initMvrvSection(),
        initAccordionPanels(),
    ]);
    updateBtcPriceCard();
    startRefreshJob("btc-price-card", refreshBtcPriceCard, 5000);
    startRefreshJob("btc-price-chart", refreshPriceChart, 60000);
    startRefreshJob("mempool-metrics", refreshMempoolMetrics, 30000);
    startRefreshJob("hashrate", refreshHashrateMetrics, 10 * 60 * 1000);
    startRefreshJob("node-count", refreshNodeMetrics, 30 * 60 * 1000);
    startRefreshJob("institutional", refreshInstitutionalMetrics, 60 * 60 * 1000);
    startRefreshJob("fear-greed", refreshFearGreed, 60 * 60 * 1000);
}

document.addEventListener("DOMContentLoaded", () => {
    initDashboard().catch((error) => {
        document.getElementById("alertBox").innerText = "Unable to load dashboard data.";
        console.error(error);
    });
});
