(function () {
    const statusEl = document.getElementById("snapshotStatus");
    const textEl = document.getElementById("snapshotText");
    const copyButton = document.getElementById("copySnapshot");
    const refreshButton = document.getElementById("refreshSnapshot");

    function setStatus(message) {
        if (statusEl) statusEl.textContent = message;
    }

    function privateKeyFromHash() {
        const hash = window.location.hash.replace(/^#/, "");
        const params = new URLSearchParams(hash);
        return params.get("key") || "";
    }

    async function loadSnapshot() {
        const key = privateKeyFromHash();
        if (!key) {
            setStatus("Missing key. Add #key=your_secret_key to the URL.");
            return;
        }
        setStatus("Loading...");
        try {
            const response = await fetch("/api/private/daily-snapshot", {
                headers: { "X-BTCWINDOW-KEY": key },
                cache: "no-store",
            });
            if (!response.ok) {
                setStatus(response.status === 401 ? "Unauthorized" : `Error ${response.status}`);
                return;
            }
            const data = await response.json();
            textEl.value = data.snapshot_text || "";
            setStatus(`Updated ${data.date || "today"}`);
        } catch (error) {
            console.error("Failed to load private daily snapshot", error);
            setStatus("Failed to load snapshot.");
        }
    }

    async function copySnapshot() {
        const value = textEl.value || "";
        if (!value) {
            setStatus("Nothing to copy.");
            return;
        }
        try {
            await navigator.clipboard.writeText(value);
            setStatus("Copied.");
        } catch (error) {
            textEl.focus();
            textEl.select();
            document.execCommand("copy");
            setStatus("Copied.");
        }
    }

    if (refreshButton) refreshButton.addEventListener("click", loadSnapshot);
    if (copyButton) copyButton.addEventListener("click", copySnapshot);
    window.addEventListener("hashchange", loadSnapshot);
    loadSnapshot();
})();
