// Scheduled maintenance banner: a black bar across the top of the desk and the
// login page, with the announcement and a countdown. See hr_services/scheduled_maintenance.py.
(function () {
	if (window.__hrs_maintenance_banner) return;
	window.__hrs_maintenance_banner = true;

	function pad(n) {
		return String(n).padStart(2, "0");
	}

	function format(seconds) {
		const d = Math.floor(seconds / 86400);
		const h = Math.floor((seconds % 86400) / 3600);
		const m = Math.floor((seconds % 3600) / 60);
		const s = seconds % 60;
		return (d ? d + "d " : "") + pad(h) + ":" + pad(m) + ":" + pad(s);
	}

	function show(data) {
		if (!data || !data.seconds_left) return;
		const endsAt = Date.now() + data.seconds_left * 1000;

		const style = document.createElement("style");
		style.textContent = `
			#hrs-maint-banner {
				position: fixed; top: 0; left: 0; right: 0; z-index: 2000;
				background: #000; color: #fff;
				font: 500 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, Arial, sans-serif;
				padding: 8px 16px;
				display: flex; align-items: center; justify-content: center; gap: 8px 20px; flex-wrap: wrap;
				text-align: center;
			}
			#hrs-maint-banner .hrs-timer {
				font-variant-numeric: tabular-nums; font-weight: 700; white-space: nowrap;
				background: rgba(255,255,255,0.14); padding: 2px 10px; border-radius: 6px;
			}
			:root { --navbar-height: calc(60px + var(--hrs-banner-h, 0px)); }
			body { padding-top: var(--hrs-banner-h, 0px) !important; }
			header.navbar.sticky-top { top: var(--hrs-banner-h, 0px); }
		`;
		document.head.appendChild(style);

		const bar = document.createElement("div");
		bar.id = "hrs-maint-banner";
		bar.setAttribute("role", "status");
		const text = document.createElement("span");
		text.textContent = "⚠ " + data.message;
		const timer = document.createElement("span");
		timer.className = "hrs-timer";
		bar.append(text, timer);
		document.body.prepend(bar);

		const fit = () =>
			document.documentElement.style.setProperty("--hrs-banner-h", bar.offsetHeight + "px");
		window.addEventListener("resize", fit);

		const tick = () => {
			const left = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
			timer.textContent = "Starts in " + format(left);
			if (left <= 0) {
				clearInterval(interval);
				// Maintenance has started: reload so the server shows the "Please wait..." page.
				setTimeout(() => window.location.reload(), 1500);
			}
		};
		const interval = setInterval(tick, 1000);
		tick();
		fit();
	}

	function load() {
		fetch("/api/method/hr_services.scheduled_maintenance.banner", {
			headers: { Accept: "application/json" },
			credentials: "same-origin",
		})
			.then((r) => (r.ok ? r.json() : null))
			.then((r) => show(r && r.message))
			.catch(() => {});
	}

	if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
	else load();
})();
