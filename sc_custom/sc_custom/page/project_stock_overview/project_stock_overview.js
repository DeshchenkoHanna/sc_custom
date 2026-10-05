// Copyright (c) 2026, SwissCluster and contributors
// For license information, please see license.txt
//
// Project Stock Overview: per item of a Project — on stock, reserved (project storage),
// on order, requested and on order for other projects — with a Schedule column (required
// dates, expected deliveries and project deadlines on one shared time axis), a status pill
// and an expandable detail row listing the documents behind the numbers.
// Data comes from sc_custom.api.project_stock_overview.get_data (one payload per project);
// warehouse / item group / search / "needs attention" are filtered here on the client.

frappe.pages["project-stock-overview"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Project Stock Overview"),
		single_column: true,
	});
	$(wrapper).addClass("pso-page");

	const state = { data: null, sort: { k: "item", dir: 1 }, open: new Set(), pops: [] };

	// ---- time axis: one month before today .. five months after today (exact dates, today sits at
	// 1/6 of the width). G px at both ends stay free for the "outside the window" points.
	const DAY = 86400000;
	const G = 16;
	let AXIS_W = 380; // measured from the header cell; the column is fluid
	const parse_date = (s) => { const d = frappe.datetime.str_to_obj(s); return new Date(d.getFullYear(), d.getMonth(), d.getDate()); };
	const short_date = (d) => moment(d).format("DD.MM");
	const user_date = (d) => moment(d).format(frappe.datetime.get_user_date_fmt().toUpperCase());
	const TODAY = parse_date(frappe.datetime.now_date());
	const AX = (() => {
		const start = moment(TODAY).subtract(1, "months").toDate(); // moment clamps e.g. 31.03 − 1 month to the end of February
		const end = moment(TODAY).add(5, "months").toDate();
		return { today: TODAY, start, end, span: Math.max(1, Math.round((end - start) / DAY)) };
	})();
	const project_end = (project) => (project && state.data && state.data.projects && state.data.projects[project] && state.data.projects[project].end) || null;
	// x position on the axis; a date outside the window gets the edge (x 0 / AXIS_W) and tail -1 / 1
	const axis_x = (d) => {
		if (d < AX.start) return { x: 0, tail: -1 };
		if (d > AX.end) return { x: AXIS_W, tail: 1 };
		return { x: G + Math.round(((d - AX.start) / DAY) / AX.span * (AXIS_W - 2 * G)), tail: 0 };
	};

	// ---- filters -------------------------------------------------------------------------
	const f_project = page.add_field({
		fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project", reqd: 1,
		change() { load(); },
	});
	const f_wh = page.add_field({
		fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse",
		get_query: () => ({ filters: { is_group: 0 } }),
		change() { render(); },
	});
	const f_grp = page.add_field({
		fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group",
		change() { render(); },
	});
	const f_q = page.add_field({ fieldname: "item", label: __("Item"), fieldtype: "Data", change() { render(); } });
	f_q.$input.on("input", frappe.utils.debounce(() => render(), 250));
	const f_attn = page.add_field({
		fieldname: "attention", label: __("Only needs attention"), fieldtype: "Check",
		change() { render(); },
	});

	page.set_primary_action(__("Refresh"), () => load(), "refresh");

	// ---- body ----------------------------------------------------------------------------
	const $body = $(`
		<div class="pso">
			<div class="pso-legend">
				<div class="items">
					<span class="k"><i class="lg vl dark"></i>${__("Required date (MRQ)")}</span>
					<span class="k"><i class="lg vl red"></i>${__("Required, not ordered or not enough")}</span>
					<span class="k"><i class="lg vl grey"></i>${__("Required, on stock (transfer)")}</span>
					<span class="k"><i class="lg seg"></i>${__("Expected later than required")}</span>
					<span class="k"><i class="lg dot blue"></i>${__("Expected (PO)")}</span>
					<span class="k"><i class="lg dot red"></i>${__("Expected, overdue")}</span>
					<span class="k"><i class="lg dot grey"></i>${__("Expected, other project")}</span>
					<span class="k"><i class="lg dot blue ring"></i>${__("Own and other projects on the same spot")}</span>
					<span class="k"><i class="lg tick"></i>${__("Today")}</span>
					<span class="k"><i class="lg vl dl"></i>${__("Project deadline")}</span>
					<span class="k"><i class="lg vl dl other"></i>${__("Other project's deadline, only where the item is on that project")}</span>
				</div>
				<span class="meta"></span>
			</div>
			<div class="pso-card">
				<div class="pso-scroll">
					<table class="pso-table">
						<thead><tr>${header_html()}</tr></thead>
						<tbody><tr><td colspan="8" class="pso-empty">${__("Select a project")}</td></tr></tbody>
					</table>
				</div>
			</div>
			<div class="pso-foot"><span class="count"></span></div>
		</div>`).appendTo(page.body);

	const $tbody = $body.find("tbody");
	const $meta = $body.find(".pso-legend .meta");
	const $count = $body.find(".pso-foot .count");
	const $axis = $body.find('th[data-k="sched"] .axis');

	// a click on a schedule marker opens its details (documents, quantities, suppliers, projects, dates)
	// Popovers live directly in <body>: no scroll area, sticky header cell or positioned container of the
	// page can clip or shift them. They close on scroll, resize, Escape, an outside click and a route change.
	const $mpop = $(`<div class="pso-mpop" role="dialog" hidden></div>`).appendTo(document.body);
	const $colpop = $(`<div class="pso-colpop" role="tooltip" hidden></div>`).appendTo(document.body);
	// put a popover below an anchor (above it when there is no room below), always inside the window
	function place(el, anchor, x_center) {
		const w = el.offsetWidth;
		const h = el.offsetHeight;
		const left = Math.max(8, Math.min(x_center - w / 2, window.innerWidth - w - 8));
		let top = anchor.bottom + 6;
		if (top + h > window.innerHeight - 8) top = Math.max(8, anchor.top - h - 6);
		el.style.left = left + "px";
		el.style.top = top + "px";
	}
	let mpop_for = null;
	const close_mpop = () => {
		if (mpop_for) $(mpop_for).removeClass("on");
		mpop_for = null;
		$mpop.prop("hidden", true);
	};
	$tbody.on("click", ".sched .mk", function (e) {
		e.stopPropagation(); // a marker click must not expand the row
		if (mpop_for === this) return close_mpop();
		const info = state.pops[+this.dataset.pop];
		if (!info) return;
		close_mpop();
		close_pops();
		$mpop.html(mpop_html(info)).prop("hidden", false);
		mpop_for = this;
		$(this).addClass("on");
		const r = this.getBoundingClientRect();
		place($mpop[0], r, r.left + r.width / 2);
	});
	$tbody.on("keydown", ".sched .mk", function (e) {
		if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $(this).trigger("click"); }
	});
	$mpop.on("click", ".x", () => close_mpop());
	$(document).on("click.pso-mpop", (e) => { if (!$(e.target).closest(".pso-mpop, .sched .mk").length) close_mpop(); });
	$(document).on("keydown.pso-mpop", (e) => { if (e.key === "Escape") close_mpop(); });
	$body.find(".pso-scroll").on("scroll", () => { close_mpop(); close_pops(); });
	frappe.router.on("change", () => { close_mpop(); close_pops(); });

	// the schedule column stretches with the window: re-measure the axis and redraw when its width changes
	const remeasure = frappe.utils.debounce(() => {
		const w = Math.floor($axis[0].getBoundingClientRect().width);
		if (w >= 380 && Math.abs(w - AXIS_W) > 2) { AXIS_W = w; render(); }
	}, 150);
	if (window.ResizeObserver) new ResizeObserver(remeasure).observe($axis[0]);
	$(window).on("resize.pso-axis", remeasure);

	// header: sort on click, definition popover on the "i"
	$body.find("thead th").on("click", function (e) {
		if ($(e.target).closest(".pop, .axis").length) return;
		const k = this.dataset.k;
		if (!k || k === "sched") return;
		state.sort = state.sort.k === k ? { k, dir: -state.sort.dir } : { k, dir: 1 };
		render();
	});
	// column definitions: one shared popover, opened below the header row and centred under the "i";
	// hovering the "i" previews it, a click pins it until an outside click, Escape or scroll
	let colpop_for = null;
	let colpop_pinned = false;
	let colpop_timer = null;
	const close_pops = () => {
		clearTimeout(colpop_timer);
		if (colpop_for) $(colpop_for).closest("th").removeClass("pop-open");
		colpop_for = null;
		colpop_pinned = false;
		$colpop.prop("hidden", true);
	};
	function open_pop(btn, pin) {
		clearTimeout(colpop_timer);
		if (colpop_for === btn) { colpop_pinned = colpop_pinned || pin; return; }
		close_pops();
		close_mpop();
		const $th = $(btn).closest("th");
		$colpop.html($th.find(".pop").html()).prop("hidden", false);
		$th.addClass("pop-open");
		colpop_for = btn;
		colpop_pinned = pin;
		const r = btn.getBoundingClientRect();
		const head = $th.closest("thead")[0].getBoundingClientRect();
		place($colpop[0], { top: r.top, bottom: head.bottom - 2 }, r.left + r.width / 2);
	}
	const close_later = () => { if (!colpop_pinned) colpop_timer = setTimeout(close_pops, 200); };
	$body.find("thead .info").each(function () {
		const btn = this;
		$(btn).on("click", (e) => { e.stopPropagation(); if (colpop_for === btn && colpop_pinned) close_pops(); else open_pop(btn, true); });
		$(btn).on("mouseenter", () => open_pop(btn, false));
		$(btn).on("mouseleave", close_later);
	});
	$colpop.on("mouseenter", () => clearTimeout(colpop_timer));
	$colpop.on("mouseleave", close_later);
	$(window).on("resize.pso-pop", () => close_pops());
	$(document).on("click.pso", (e) => { if (!$(e.target).closest(".pso th .info, .pso-colpop").length) close_pops(); });
	$(document).on("keydown.pso", (e) => { if (e.key === "Escape") close_pops(); });

	// row expand
	$tbody.on("click", "tr.pso-row", function (e) {
		if ($(e.target).closest("a, .mk").length) return;
		const code = this.dataset.code;
		state.open.has(code) ? state.open.delete(code) : state.open.add(code);
		render();
	});

	// The page as a whole must never scroll: the desk scrolls inside .main-section (100vh), so the
	// table area gets exactly the height that is left in it. Then the page head, the filters, the
	// legend and the table header stay where they are and only the table body scrolls.
	const $scroll = $body.find(".pso-scroll");
	const scroll_parent = (el) => {
		for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
			const oy = getComputedStyle(p).overflowY;
			if (oy === "auto" || oy === "scroll" || oy === "overlay") return p;
		}
		return document.scrollingElement || document.documentElement;
	};
	function fit_table() {
		const el = $scroll[0];
		if (!el || !el.offsetParent) return; // page not visible
		const outer = scroll_parent(el);
		const is_doc = outer === document.scrollingElement || outer === document.documentElement;
		$(is_doc ? window : outer).off("scroll.pso-mpop").on("scroll.pso-mpop", () => { close_mpop(); close_pops(); });
		const view = is_doc ? window.innerHeight : outer.clientHeight;
		const top = el.getBoundingClientRect().top - (is_doc ? 0 : outer.getBoundingClientRect().top) + outer.scrollTop;
		const below = ($body.find(".pso-foot").outerHeight(true) || 0) + 8; // count line + bottom padding of .pso
		const h = Math.max(200, Math.floor(view - top - below));
		el.style.maxHeight = h + "px";
		// whatever the desk adds below the page (paddings, margins) must not make it scroll either
		const excess = outer.scrollHeight - outer.clientHeight;
		if (excess > 0) {
			el.style.maxHeight = Math.max(200, h - excess) + "px";
			if (outer.scrollHeight - outer.clientHeight >= excess) el.style.maxHeight = h + "px"; // that overflow is not ours
		}
	}

	// keep page head + filters pinned; the table area fills the rest of the window
	function stick_header() {
		const nav = (document.querySelector(".navbar") || {}).offsetHeight || 0;
		const $head = $(wrapper).find(".page-head").first();
		$head.css({ top: nav + "px" });
		const $form = $(wrapper).find(".page-form");
		$form.css({ top: nav + ($head.outerHeight() || 0) + "px" });
		wrapper.style.setProperty("--pso-stick", nav + ($head.outerHeight() || 0) + ($form.outerHeight() || 0) + "px");
		fit_table();
	}
	stick_header();
	setTimeout(stick_header, 300);
	$(window).on("resize.pso", stick_header);
	// refit when the filters or the legend wrap to another line, and when the page is shown again
	if (window.ResizeObserver) {
		const ro = new ResizeObserver(() => stick_header());
		[$body.find(".pso-legend")[0], $(wrapper).find(".page-form")[0], $(wrapper).find(".page-head")[0]].filter(Boolean).forEach((el) => ro.observe(el));
	}
	frappe.pages["project-stock-overview"].on_page_show = () => stick_header();

	// ---- data ----------------------------------------------------------------------------
	function load() {
		const project = f_project.get_value();
		if (!project) {
			state.data = null;
			render();
			return;
		}
		frappe.call({
			method: "sc_custom.api.project_stock_overview.get_data",
			args: { project },
			freeze: true,
			freeze_message: __("Loading stock of {0}...", [project]),
		}).then((r) => {
			state.data = r.message;
			state.open.clear();
			try { localStorage.setItem("pso:project", project); } catch (e) { /* ignore */ }
			render();
		});
	}

	function rows_for(project, wh) {
		const st2prj = state.data.storage_to_project || {};
		const out = [];
		for (const it of state.data.items) {
			const reserved = flt(it.reserved[project] || 0);
			const order = sum(it.po.filter((p) => p.prj === project).map((p) => p.qty));
			const mr = sum(it.mrl.filter((m) => m.prj === project).map((m) => m.qty));
			if (!(reserved > 0 || order > 0 || mr > 0)) continue;
			const stock_rows = it.stock.filter((s) => !wh || s.wh === wh);
			const stock = sum(stock_rows.map((s) => s.qty));
			const reserved_others = sum(Object.entries(it.reserved).filter(([p]) => p !== project).map(([, q]) => q));
			const other = sum(it.po.filter((p) => p.prj !== project).map((p) => p.qty));
			const free = Math.max(stock - reserved - reserved_others, 0);
			let status;
			if (reserved > 0 && order === 0 && mr === 0) status = "ok";
			else if (reserved > 0) status = "partial";
			else if (order > 0) status = "wait";
			else if (free >= mr) status = "transfer"; // requested qty is on stock and free: move it to the project storage
			else status = "short"; // requested qty is not on stock (or not enough): must be ordered
			// `reserved` on the payload is the per-project dictionary; keep it as reserved_by,
			// the same key on the row is the total for the selected project
			out.push({ ...it, reserved_by: it.reserved, st2prj, stock, stock_rows, reserved, reserved_others, free, order, mr, other, status });
		}
		return out;
	}

	// ---- render --------------------------------------------------------------------------
	const STATUS = {
		ok: ["green", __("Reserved on stock")],
		partial: ["orange", __("Partly on stock")],
		wait: ["blue", __("Ordered, waiting for delivery")],
		transfer: ["yellow", __("Requested, not transferred")],
		short: ["red", __("Requested, not ordered")],
	};
	const ORDER = { short: 0, transfer: 1, wait: 2, partial: 3, ok: 4 };

	function render() {
		render_rows();
		fit_table();
	}

	function render_rows() {
		close_mpop();
		state.pops = [];
		$body.find("thead th").each(function () {
			$(this).toggleClass("sorted", this.dataset.k === state.sort.k);
			$(this).find(".arrow").remove();
			if (this.dataset.k === state.sort.k) $(this).find(".hd").append(`<span class="arrow">${state.sort.dir > 0 ? "▲" : "▼"}</span>`);
		});

		if (!state.data) {
			$tbody.html(`<tr><td colspan="8" class="pso-empty">${__("Select a project")}</td></tr>`);
			$axis.html(axis_html(null));
			$meta.text("");
			$count.text("");
			return;
		}
		const project = state.data.project;
		const wh = f_wh.get_value();
		const grp = f_grp.get_value();
		const q = (f_q.get_value() || "").trim().toLowerCase();
		const attn = !!f_attn.get_value();

		const all = rows_for(project, wh);
		let rows = all;
		if (grp) rows = rows.filter((r) => r.group === grp);
		if (q) rows = rows.filter((r) => (r.code + " " + r.name).toLowerCase().includes(q));
		if (attn) rows = rows.filter((r) => r.status !== "ok");
		const { k, dir } = state.sort;
		const key = (r) => (k === "item" ? r.code : k === "status" ? ORDER[r.status] : r[k]);
		rows.sort((a, b) => (key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * dir);

		$axis.html(axis_html(project));
		$meta.text(__("{0} · snapshot {1}", [project, frappe.datetime.str_to_user(state.data.generated)]));
		$count.text(__("{0} of {1} items", [rows.length, all.length]));

		if (!rows.length) {
			$tbody.html(`<tr><td colspan="8" class="pso-empty">${__("No items match the current filters")}</td></tr>`);
			return;
		}
		$tbody.html(rows.map((r) => row_html(r, project) + (state.open.has(r.code) ? detail_html(r, project) : "")).join(""));
	}

	function row_html(r, project) {
		const [color, label] = STATUS[r.status];
		return `<tr class="pso-row status-${r.status}${state.open.has(r.code) ? " open" : ""}" data-code="${esc(r.code)}">
			<td class="item"><span class="exp">▶</span> <a class="id" href="${doc_url("Item", r.code)}" target="_blank" rel="noopener">${esc(r.code)}</a><div class="nm">${esc(r.name)}</div><div class="grp">${esc(r.group)}</div></td>
			<td class="num${r.stock ? "" : " zero"}">${fmt(r.stock)}</td>
			<td class="num reserved${r.reserved ? "" : " zero"}">${fmt(r.reserved)}</td>
			<td class="num order${r.order ? "" : " zero"}">${fmt(r.order)}</td>
			<td class="num mr${r.mr ? " nz" : " zero"}">${fmt(r.mr)}</td>
			<td class="num other${r.other ? "" : " zero"}">${fmt(r.other)}</td>
			<td class="sched-cell">${sched_html(r, project)}</td>
			<td class="status-cell"><span class="indicator-pill ${color}">${label}</span></td>
		</tr>`;
	}

	// ---- schedule column -----------------------------------------------------------------
	// month labels + today + the selected project's deadline, drawn once per render into the header cell
	function axis_html(project) {
		const parts = [];
		// a tick on every 1st of a month inside the window; the year on the first label and on January
		let first = true;
		const first_tick = new Date(AX.start.getFullYear(), AX.start.getMonth() + (AX.start.getDate() === 1 ? 0 : 1), 1);
		if (AX.start.getDate() !== 1 && axis_x(first_tick).x - G >= 40) {
			// the window starts inside a month: name that month at the left edge as well
			parts.push(`<span class="m" style="left:${G}px">${moment(AX.start).format("MMM YY")}</span>`);
			first = false;
		}
		for (let d = first_tick; d <= AX.end; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
			const { x } = axis_x(d);
			parts.push(`<i class="mt" style="left:${x}px"></i>`);
			if (x > AXIS_W - 30) continue; // no room for a label at the right edge
			const cur = d.getMonth() === AX.today.getMonth() && d.getFullYear() === AX.today.getFullYear();
			parts.push(`<span class="m${cur ? " cur" : ""}" style="left:${x}px">${moment(d).format(first || d.getMonth() === 0 ? "MMM YY" : "MMM")}</span>`);
			first = false;
		}
		// second row: "today" and the selected project's deadline; the two labels never overlap
		const est = (text) => Math.ceil(text.length * 5.8) + 4; // ~ width of a 10 px label
		const box = (x, side, w) => (side === "right" ? [x - 4 - w, x - 4] : side === "left" ? [x + 4, x + 4 + w] : [x - w / 2, x + w / 2]);
		const css = (x, side) => (side === "right" ? `right:${AXIS_W - x + 4}px;transform:none` : side === "left" ? `left:${x + 4}px;transform:none` : `left:${x}px`);
		const hit = (a, b) => !!(a && b) && a[0] < b[1] + 6 && b[0] < a[1] + 6;
		const fits = (b) => b[0] >= -4 && b[1] <= AXIS_W + 4;

		let dl_part = "";
		let dl_box = null;
		const end = project_end(project);
		if (end) {
			const d = parse_date(end);
			const p = axis_x(d);
			const text = `${esc(project)} · ${p.tail < 0 ? "‹ " : ""}${p.tail ? user_date(d) : short_date(d)}${p.tail > 0 ? " ›" : ""}`;
			const w = est(text);
			let x = p.x;
			const side = p.tail < 0 ? "left" : p.tail > 0 ? "right" : p.x + 4 + w > AXIS_W ? "right" : "left";
			dl_box = box(x, side, w);
			const tx = axis_x(AX.today);
			const tw = est(__("today"));
			if (p.tail < 0 && !tx.tail && hit(dl_box, box(tx.x, "center", tw))) {
				// a deadline before the window has no position of its own: put its label right after "today"
				x = Math.round(tx.x + tw / 2 + 4);
				dl_box = box(x, side, w);
			}
			if (!p.tail) parts.push(`<i class="dlt" style="left:${p.x}px"></i>`);
			dl_part = `<span class="dl" style="${css(x, side)}" title="${__("Planned end of {0}", [esc(project)])} ${user_date(d)}">${text}</span>`;
		}
		const t = axis_x(AX.today);
		const today_title = `${__("today")} ${user_date(AX.today)}`;
		if (t.tail) {
			parts.push(`<span class="today" style="${t.tail < 0 ? "left:0" : "right:0"};transform:none" title="${today_title}">${t.tail < 0 ? "‹ " : ""}${__("today")}${t.tail > 0 ? " ›" : ""}</span>`);
		} else {
			const w = est(__("today"));
			const side = ["center", "left", "right"].find((sd) => fits(box(t.x, sd, w)) && !hit(box(t.x, sd, w), dl_box)) || "center";
			parts.push(`<i class="tt" style="left:${t.x}px"></i><span class="today" style="${css(t.x, side)}" title="${today_title}">${__("today")}</span>`);
		}
		parts.push(dl_part);
		return parts.join("");
	}

	// lines of one kind on the same date are one marker; markers closer than 24 px merge too
	function markers(lines) {
		const by_date = new Map();
		for (const l of lines) {
			if (!l.date) continue;
			const g = by_date.get(l.date);
			if (g) { g.qty = flt(g.qty + l.qty, 3); g.docs.push(l); }
			else by_date.set(l.date, { date: l.date, d: parse_date(l.date), qty: flt(l.qty, 3), docs: [l] });
		}
		const sorted = [...by_date.values()].sort((a, b) => a.d - b.d).map((m) => ({ ...m, ...axis_x(m.d), dates: [m.date] }));
		const out = [];
		for (const m of sorted) {
			const last = out[out.length - 1];
			if (last && m.x - last.x < 24) { last.qty = flt(last.qty + m.qty, 3); last.docs.push(...m.docs); last.dates.push(m.date); }
			else out.push(m);
		}
		return out;
	}
	const date_label = (m) => `${m.tail < 0 ? "‹ " : ""}${m.tail ? user_date(m.d) : short_date(m.d)}${m.tail > 0 ? " ›" : ""}${m.dates.length > 1 ? ` +${m.dates.length - 1}` : ""}`;
	const docs_title = (m, what) => m.docs.map((l) => `${what} ${fmt(l.qty)} · ${l.po || l.mr}${l.prj ? " · " + l.prj : ""} · ${user_date(parse_date(l.date))}`).join("\n");

	function sched_html(r, project) {
		const parts = [`<i class="base"></i>`];
		const projects = (state.data && state.data.projects) || {};

		// deadlines: the selected project in every row, another project only where this item is on it
		const own_end = projects[project] && projects[project].end;
		if (own_end) {
			const p = axis_x(parse_date(own_end));
			if (!p.tail) parts.push(`<i class="vl dl" style="left:${p.x}px" title="${esc(project)} ${__("deadline")} ${user_date(parse_date(own_end))}"></i>`);
		}
		const others = new Set([
			...r.po.filter((l) => l.prj && l.prj !== project).map((l) => l.prj),
			...r.mrl.filter((l) => l.prj && l.prj !== project).map((l) => l.prj),
			...Object.keys(r.reserved_by).filter((p) => p !== project && r.reserved_by[p] > 0),
		]);
		const dl_labels = [];
		for (const p of others) {
			const end = projects[p] && projects[p].end;
			if (!end) continue;
			const pos = axis_x(parse_date(end));
			if (pos.tail) continue;
			parts.push(`<i class="vl dl other" style="left:${pos.x}px" title="${esc(p)} ${__("deadline")} ${user_date(parse_date(end))}"></i>`);
			dl_labels.push({ cls: "top0", x: pos.x, gap: 4, text: `${p} · ${short_date(parse_date(end))}` });
		}

		const tt = axis_x(AX.today);
		if (!tt.tail) parts.push(`<i class="tick" style="left:${tt.x}px"></i>`);

		const own_mr = r.mrl.filter((l) => l.prj === project && l.date);
		const own_po = r.po.filter((l) => l.prj === project && l.date);
		const oth_po = r.po.filter((l) => l.prj !== project && l.date);
		const side_of = (l) => axis_x(parse_date(l.date)).tail; // -1 before, 0 inside, 1 after the window
		const req_cls = r.status === "transfer" ? "grey" : (r.status === "short" || r.order + r.free < r.mr) ? "red" : "dark";

		// required dates (MRQ lines of this project) inside the window
		const req_labels = [];
		for (const m of markers(own_mr.filter((l) => !side_of(l)))) {
			parts.push(`<i class="vl req ${req_cls}" style="left:${m.x}px" title="${esc(docs_title(m, __("required")))}"></i>`);
			req_labels.push({ cls: `top1 ${req_cls}`, x: m.x, gap: 5, text: `req ${fmt(m.qty)} · ${date_label(m)}` });
		}

		// labels of the vertical lines sit at their top: required dates first, then other projects' deadlines;
		// a label that would touch another one drops to a second row, and is left out only when both are taken
		const label_rows = [[], []];
		for (const lb of [...req_labels, ...dl_labels]) {
			const w = Math.ceil(lb.text.length * 5.8) + 4; // ~ label width
			const a = lb.x + lb.gap + w > AXIS_W ? lb.x - lb.gap - w : lb.x + lb.gap; // left of its line at the right edge
			const row = [0, 1].find((rw) => label_rows[rw].every(([l, r]) => a + w + 4 <= l || a >= r + 4));
			if (row === undefined) continue;
			label_rows[row].push([a, a + w]);
			parts.push(`<span class="lb ${lb.cls}${row ? " r2" : ""}" style="left:${Math.round(a)}px">${esc(lb.text)}</span>`);
		}

		// one marker per spot, no text on the line; its details open on click
		const marker_html = (cls, ring, x, title, ev, groups = group_events(ev)) => {
			const idx = state.pops.push({ title, groups }) - 1;
			return `<span class="mk ${cls}${ring ? " ring" : ""}" data-pop="${idx}" style="left:${x}px" role="button" tabindex="0" aria-label="${esc(title)}"></span>`;
		};

		// deliveries inside the window: everything on the same spot is one marker,
		// the own delivery as the dot, other projects' deliveries as a grey ring around it
		const SPOT = 16; // px
		const events = [
			...own_po.filter((l) => !side_of(l)).map((l) => ({ ...l, kind: "exp", doc: l.po })),
			...oth_po.filter((l) => !side_of(l)).map((l) => ({ ...l, kind: "oth", doc: l.po })),
		].map((e) => { const d = parse_date(e.date); return { ...e, d, x: axis_x(d).x }; })
			.sort((a, b) => a.x - b.x || a.d - b.d);
		const spots = [];
		let last_label_end = -Infinity;
		for (const e of events) {
			const last = spots[spots.length - 1];
			if (last && e.x - last.ev[0].x < SPOT) last.ev.push(e);
			else spots.push({ ev: [e] });
		}
		for (const sp of spots) {
			const own = sp.ev.filter((e) => e.kind === "exp");
			const x = own.length ? own[0].x : sp.ev[0].x;
			for (const e of sp.ev) e.sx = x;
			const cls = !own.length ? "grey" : own.some((e) => e.d < AX.today) ? "red" : "blue";
			const ds = sp.ev.map((e) => e.d).sort((a, b) => a - b);
			const title = +ds[0] === +ds[ds.length - 1] ? user_date(ds[0]) : `${short_date(ds[0])} – ${user_date(ds[ds.length - 1])}`;
			const groups = group_events(sp.ev);
			parts.push(marker_html(cls, own.length && own.length < sp.ev.length, x, title, sp.ev, groups));
			if (groups.length === 1) {
				const text = `exp ${fmt(groups[0].qty)}`;
				const w = Math.ceil(text.length * 5.8) + 4; // ~ label width
				const lx = Math.round(Math.max(0, Math.min(x - w / 2, AXIS_W - w))); // centred under the dot
				if (lx >= last_label_end + 4) { // a label that would touch its left neighbour stays in the popup
					parts.push(`<span class="lb bot${cls === "red" ? " red" : cls === "grey" ? " grey" : ""}" style="left:${lx}px">${text}</span>`);
					last_label_end = lx + w;
				}
			}
		}

		// the alert: a delivery expected after the required date (a date outside the window counts as the edge)
		if (own_mr.length && own_po.length) {
			const rd = new Date(Math.min(...own_mr.map((l) => parse_date(l.date))));
			const last_exp = own_po.reduce((a, l) => (!a || l.date > a.date ? l : a), null);
			const ed = parse_date(last_exp.date);
			const at = events.find((e) => e.kind === "exp" && e.po === last_exp.po && e.date === last_exp.date);
			const rx = axis_x(rd).x;
			const ex = at ? at.sx : axis_x(ed).x;
			if (ed > rd && ex > rx) parts.splice(1, 0, `<i class="delay" style="left:${rx}px;width:${ex - rx}px" title="${__("Expected after the required date")}"></i>`);
		}

		// everything outside the window: one marker per side at the edge, no number; details on click
		const rank = { grey: 0, dark: 1, blue: 2, red: 3 };
		for (const side of [-1, 1]) {
			const ev = [
				...own_mr.filter((l) => side_of(l) === side).map((l) => ({ ...l, kind: "req", doc: l.mr, cls: req_cls })),
				...own_po.filter((l) => side_of(l) === side).map((l) => ({ ...l, kind: "exp", doc: l.po, cls: parse_date(l.date) < AX.today ? "red" : "blue" })),
				...oth_po.filter((l) => side_of(l) === side).map((l) => ({ ...l, kind: "oth", doc: l.po, cls: "grey" })),
			];
			if (!ev.length) continue;
			const own = ev.filter((e) => e.kind !== "oth");
			const cls = own.reduce((a, e) => (rank[e.cls] > rank[a] ? e.cls : a), "grey");
			const title = side < 0 ? __("Before {0} (outside the window)", [user_date(AX.start)]) : __("After {0} (outside the window)", [user_date(AX.end)]);
			parts.push(marker_html(cls, own.length && own.length < ev.length, side < 0 ? 0 : AXIS_W, title, ev));
		}
		return `<div class="sched">${parts.join("")}</div>`;
	}

	// lines of one marker grouped per document (kind + document + project), sorted by date
	function group_events(ev) {
		const groups = new Map();
		for (const e of ev) {
			const k = `${e.kind}|${e.doc}|${e.prj}`;
			const g = groups.get(k);
			if (g) { g.qty = flt(g.qty + e.qty, 3); g.dates.add(e.date); }
			else groups.set(k, { kind: e.kind, doc: e.doc, sup: e.sup, prj: e.prj, qty: flt(e.qty, 3), dates: new Set([e.date]) });
		}
		const first = (g) => [...g.dates].sort()[0];
		return [...groups.values()].sort((a, b) => first(a).localeCompare(first(b)));
	}

	// the click popup of a marker
	function mpop_html(info) {
		const dates = (g) => [...g.dates].sort().map((d) => user_date(parse_date(d))).join(", ");
		const overdue = (g) => [...g.dates].some((d) => parse_date(d) < AX.today);
		const of = (kind) => info.groups.filter((g) => g.kind === kind);
		const sec = (title, rows) => (rows.length ? `<div class="sec">${title}</div><table><tbody>${rows.join("")}</tbody></table>` : "");
		return `<div class="hd"><b>${esc(info.title)}</b><button type="button" class="x" aria-label="${__("Close")}">×</button></div>`
			+ sec(__("Required · this project"), of("req").map((g) =>
				`<tr><td>${doc_link("Material Request", g.doc)}</td><td class="num">${fmt(g.qty)}</td><td>${dates(g)}</td></tr>`))
			+ sec(__("Expected · this project"), of("exp").map((g) =>
				`<tr><td>${doc_link("Purchase Order", g.doc)}</td><td class="tag">${esc(g.sup || "")}</td><td class="num">${fmt(g.qty)}</td><td>${dates(g)}${overdue(g) ? ` <span class="late">${__("overdue")}</span>` : ""}</td></tr>`))
			+ sec(__("Expected · other projects"), of("oth").map((g) =>
				`<tr><td>${doc_link("Purchase Order", g.doc)}</td><td class="prj">${esc(g.prj || __("no project"))}</td><td class="tag">${esc(g.sup || "")}</td><td class="num">${fmt(g.qty)}</td><td>${dates(g)}</td></tr>`));
	}

	function detail_html(r, project) {
		const st_name = (s) => (r.st2prj[s] ? `${r.st2prj[s]} · ${__("project storage")}` : s || "—");
		const stock = r.stock_rows.map((s) => `<tr><td>${esc(s.wh)}</td><td class="tag${r.st2prj[s.storage] === project ? " this" : ""}">${esc(st_name(s.storage))}</td><td class="num">${fmt(s.qty)}</td></tr>`).join("");

		const res = Object.entries(r.reserved_by).filter(([, q]) => q > 0);
		const res_html = res.map(([p, q]) => `<tr><td class="${p === project ? "this" : "oth"}">${esc(p)}</td><td class="num">${fmt(q)}</td></tr>`).join("");

		const first_own = (a, b) => (a.prj === project ? 0 : 1) - (b.prj === project ? 0 : 1) || a.date.localeCompare(b.date);
		const mrs = group_docs(r.mrl, "mr").sort(first_own);
		const mr_html = mrs.map((m) => `<tr><td>${doc_link("Material Request", m.mr)}</td><td class="${m.prj === project ? "this" : "oth"}">${m.prj === project ? __("this project") : esc(m.prj || __("no project"))}</td><td class="num ${m.prj === project ? "this" : "oth"}">${fmt(m.qty)}</td><td class="tag">${esc(m.date)}</td></tr>`).join("")
			+ (mrs.length ? `<tr><td class="sum" colspan="2">${__("Requested for this project")}</td><td class="num sum">${fmt(r.mr)}</td><td class="sum"></td></tr>` : "");

		const pos = group_docs(r.po, "po").sort(first_own);
		const po_html = pos.map((p) => `<tr><td>${doc_link("Purchase Order", p.po)}</td><td class="tag">${esc(p.sup)}</td><td class="${p.prj === project ? "this" : "oth"}">${p.prj === project ? __("this project") : esc(p.prj || __("no project"))}</td><td class="num ${p.prj === project ? "this" : "oth"}">${fmt(p.qty)}</td><td class="tag">${esc(p.date)}</td></tr>`).join("")
			+ (pos.length ? `<tr><td class="sum" colspan="3">${__("On order for this project")}</td><td class="num sum">${fmt(r.order)}</td><td class="sum"></td></tr><tr><td class="sum" colspan="3">${__("On order, other")}</td><td class="num sum">${fmt(r.other)}</td><td class="sum"></td></tr>` : "");

		const block = (title, has, head, body, none) => has
			? `<div class="mini"><h4>${title}</h4><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`
			: `<div class="mini"><h4>${title}</h4><div class="none">${none}</div></div>`;

		return `<tr class="detail"><td colspan="8"><div class="detail-grid">
			${block(__("On stock by warehouse / storage"), r.stock_rows.length, `<th>${__("Warehouse")}</th><th>${__("Storage")}</th><th class="num">${__("Qty")}</th>`, stock, __("Not on stock"))}
			${block(__("Reserved for projects"), res.length, `<th>${__("Project")}</th><th class="num">${__("Qty")}</th>`, res_html, __("Not in any project storage"))}
			${block(__("Open material requests"), mrs.length, `<th>MRQ</th><th>${__("Project")}</th><th class="num">${__("Qty")}</th><th>${__("Required by")}</th>`, mr_html, __("No open material requests"))}
			${block(__("Open orders"), pos.length, `<th>PO</th><th>${__("Supplier")}</th><th>${__("Project")}</th><th class="num">${__("Qty")}</th><th>${__("Expected")}</th>`, po_html, __("No open purchase orders"))}
		</div></td></tr>`;
	}

	// ---- helpers -------------------------------------------------------------------------
	function header_html() {
		const th = (k, label, cls, title, text, src, extra = "", label_html = "") => `<th data-k="${k}" class="${cls}"><span class="hd"><span class="lbl">${label_html || label}</span><button type="button" class="info" aria-label="${__("Definition of {0}", [label])}">i</button></span>${extra}
			<div class="pop"><b>${title}</b>${text ? `<div class="txt">${text}</div>` : ""}${src ? `<div class="src"><span class="src-l">${__("Source")}</span>${src}</div>` : ""}</div></th>`;
		return [
			th("item", __("Item"), "", __("Item"), __("Item code (link to the Item master), item name and item group. An item is listed when it has stock in the project storage, an open Purchase Order line for the project or an open Material Request line for the project."), `<code>Item</code>`),
			th("stock", __("On stock/In manufacturing"), "num", __("On stock/In manufacturing"), __("Current quantity over all warehouses and storages, including the project storages and the Work In Progress warehouse (material issued to manufacturing). Independent of the selected project; the Warehouse filter narrows it to one warehouse."), __("Stock Ledger Entry balance per warehouse and storage (a Stock Reconciliation sets the balance); warehouse totals equal the Stock Balance report"), "", `${__("On stock/")}<br>${__("In manufacturing")}`),
			th("reserved", __("Reserved"), "num", __("Reserved"), __("Quantity physically in the project's own storage, i.e. the Storage whose name starts with the project code. Goods received for a project are always put there."), __("Stock Ledger Entry balance for the project storage")),
			th("order", __("On order"), "num", __("On order"), __("Ordered from suppliers for this project and not yet received: submitted Purchase Orders that are not Closed or Completed, only lines where received is less than ordered. The project is read from the order line."), `<code>Purchase Order Item</code>: (qty − received_qty) × conversion_factor, project = ${__("selected project")}`),
			th("mr", __("Requested"), "num", __("Requested"), __("Asked for in Material Requests for this project but not yet turned into a Purchase Order: submitted requests of type Purchase, not Stopped or Cancelled, only the unordered part of each line."), `<code>Material Request Item</code>: stock_qty − ordered_qty × conversion_factor, project = ${__("selected project")}`),
			th("other", __("On order, other"), "num", __("On order, other"), __("Same item ordered from suppliers and not yet received, but for a different project or with no project on the order line."), `<code>Purchase Order Item</code>: (qty − received_qty) × conversion_factor, project ≠ ${__("selected project")}`),
			th("sched", __("Schedule"), "sched", `${user_date(AX.start)} – ${user_date(AX.end)}`, "", "", `<div class="axis"></div>`),
			th("status", __("Status"), "", __("Status"), `${__("Reserved on stock")}: Reserved &gt; 0, ${__("nothing on order or requested")}.<br>${__("Partly on stock")}: Reserved &gt; 0 ${__("and something still on order or requested")}.<br>${__("Ordered, waiting for delivery")}: Reserved = 0, On order &gt; 0.<br>${__("Requested, not transferred")}: ${__("only Requested")} &gt; 0 ${__("and the free stock covers it — move it to the project storage")}.<br>${__("Requested, not ordered")}: ${__("only Requested")} &gt; 0 ${__("and the free stock does not cover it")}.`, __("Derived from Reserved, On order and Requested")),
		].join("");
	}

	function group_docs(lines, key) {
		const m = new Map();
		for (const l of lines) {
			const k = l[key] + "|" + l.prj;
			const g = m.get(k);
			if (g) { g.qty = flt(g.qty + l.qty, 3); if (l.date && (!g.date || l.date < g.date)) g.date = l.date; }
			else m.set(k, { ...l });
		}
		return [...m.values()];
	}

	const sum = (a) => flt(a.reduce((x, y) => x + y, 0), 3);
	const esc = (s) => frappe.utils.escape_html(String(s ?? ""));
	const doc_url = (doctype, name) => `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;
	const doc_link = (doctype, name) => `<a href="${doc_url(doctype, name)}" target="_blank" rel="noopener">${esc(name)}</a>`;
	const decimal_str = get_number_format_info(frappe.boot.sysdefaults.number_format || "#,###.##").decimal_str || ".";
	const fmt = (n) => {
		if (!n) return "0";
		const s = format_number(n, null, 3);
		const re = new RegExp(`(\\${decimal_str}\\d*?[1-9])0+$|\\${decimal_str}0+$`);
		return s.replace(re, "$1");
	};

	// ---- start ---------------------------------------------------------------------------
	let initial = (frappe.route_options && frappe.route_options.project) || null;
	frappe.route_options = null;
	if (!initial) { try { initial = localStorage.getItem("pso:project"); } catch (e) { /* ignore */ } }
	{ const w = Math.floor($axis[0].getBoundingClientRect().width); if (w >= 380) AXIS_W = w; }
	$axis.html(axis_html(null));
	if (initial) {
		f_project.set_value(initial);
		load();
	}
};
