// Recruitment Status Board: runs inside the custom block's shadow root (root_element).
const COLORS = {
	"New Request": "#7575ff", "Pending Candidate Signature": "#ffa00a", "Offer Signed": "#29cd42",
	"Pending with Agency": "#ff5858", "Visa Issued": "#2490ef", "Transfer in Process": "#ecad4b",
	"Transfer Completed": "#3bb68f", "Contract Sent": "#9c45e3", "Employee Created": "#28a745",
	"Rejected by Candidate": "#e24c4c", "Withdrawn": "#8d99a6",
	"Legacy Draft": "#b8c2cc", "Legacy": "#98a6b3", "Legacy Cancelled": "#c9b5b5",
};
const $ = (s) => root_element.querySelector(s);
const $$ = (s) => Array.from(root_element.querySelectorAll(s));
const KEY = "recruitment-status-board-filters";
let options_loaded = false;

function get_filters() {
	const f = {};
	$$("[data-f]").forEach((el) => {
		const v = el.type === "checkbox" ? (el.checked ? 1 : 0) : el.value;
		if (v) f[el.dataset.f] = v;
	});
	return f;
}

function set_filters(f) {
	$$("[data-f]").forEach((el) => {
		if (el.type === "checkbox") el.checked = !!f[el.dataset.f];
		else el.value = f[el.dataset.f] || "";
	});
}

function open_list(extra) {
	const f = get_filters();
	const opts = Object.assign({}, extra);
	if (f.category) opts.custom_candidate_category = f.category;
	if (f.recruiter) opts.custom_recruiter = f.recruiter;
	if (f.project) opts.custom_project = f.project;
	if (f.from_date && f.to_date) opts.creation = ["Between", [f.from_date, f.to_date]];
	else if (f.from_date) opts.creation = [">=", f.from_date];
	else if (f.to_date) opts.creation = ["<=", f.to_date + " 23:59:59"];
	frappe.route_options = opts;
	frappe.set_route("List", "Job Offer", "List");
}

function fill_options(o) {
	if (options_loaded) return;
	const rec = $('[data-f="recruiter"]'), proj = $('[data-f="project"]');
	(o.recruiters || []).forEach((r) => rec.add(new Option(r, r)));
	(o.projects || []).forEach((p) => proj.add(new Option(p.label, p.value)));
	options_loaded = true;
}

function tile(s, total) {
	const pct = total ? Math.round((s.count * 100) / total) : 0;
	const cats = s.group === "old"
		? Object.entries(s.by_status || {}).map(([k, v]) => `<span class="rsb-chip" data-status="${frappe.utils.escape_html(k)}">${frappe.utils.escape_html(k)}: ${v}</span>`)
		: ["Overseas", "Local", "Saudi"].filter((c) => s[c.toLowerCase()]).map((c) => `<span class="rsb-chip" data-cat="${c}">${c}: ${s[c.toLowerCase()]}</span>`);
	const el = document.createElement("div");
	el.className = "rsb-tile" + (s.count ? "" : " empty");
	el.style.setProperty("--c", COLORS[s.stage] || "#999");
	el.innerHTML = `<div class="rsb-stage">${__(s.stage)}</div>
		<div class="rsb-count">${s.count}<span class="rsb-pct">${total ? pct + "%" : ""}</span></div>
		<div class="rsb-cats">${cats.join("")}</div>`;
	el.addEventListener("click", (e) => {
		const chip = e.target.closest(".rsb-chip");
		const extra = { workflow_state: s.stage };
		if (chip && chip.dataset.cat) extra.custom_candidate_category = chip.dataset.cat;
		if (chip && chip.dataset.status) extra.status = chip.dataset.status;
		open_list(extra);
	});
	return el;
}

function render(data) {
	fill_options(data.options || {});
	const stages = data.stages || [];
	for (const group of ["in_progress", "finished", "old"]) {
		const box = $(`.rsb-group[data-group="${group}"]`);
		const mine = stages.filter((s) => s.group === group);
		box.hidden = !mine.length;
		const total = mine.reduce((a, s) => a + s.count, 0);
		box.querySelector(".rsb-total").textContent = mine.length ? `(${total})` : "";
		const tiles = box.querySelector(".rsb-tiles");
		tiles.innerHTML = "";
		mine.forEach((s) => tiles.appendChild(tile(s, total)));
	}
	// one bar across all new-flow offers
	const flow = stages.filter((s) => s.group !== "old" && s.count);
	const sum = flow.reduce((a, s) => a + s.count, 0);
	const bar = $(".rsb-bar");
	bar.innerHTML = "";
	flow.forEach((s) => {
		const seg = document.createElement("span");
		seg.style.width = (s.count * 100) / sum + "%";
		seg.style.background = COLORS[s.stage];
		seg.title = `${__(s.stage)}: ${s.count}`;
		seg.addEventListener("click", () => open_list({ workflow_state: s.stage }));
		bar.appendChild(seg);
	});
	bar.hidden = !sum;
}

function load() {
	const f = get_filters();
	try { localStorage.setItem(KEY, JSON.stringify(f)); } catch (e) {}
	frappe.call({ method: "hr_services.recruitment.dashboard.status_board", args: { filters: f } })
		.then((r) => render(r.message || {}));
}

let saved = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
$$("[data-f]").forEach((el) => el.addEventListener("change", load));
$(".rsb-reset").addEventListener("click", () => { set_filters({}); load(); });
frappe.call({ method: "hr_services.recruitment.dashboard.status_board", args: { filters: {} } }).then((r) => {
	fill_options((r.message || {}).options || {});
	set_filters(saved);
	load();
});
