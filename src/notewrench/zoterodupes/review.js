"use strict";
(() => {
  const data = JSON.parse(document.getElementById("bridge-data").textContent);
  const review = data.review;
  let state = JSON.parse(JSON.stringify(data.decisions));
  const pairs = new Map(review.pairs.map(pair => [pair.id, pair]));
  const storageKey = "library-bridge:" + review.review_id;
  let selected = review.clusters[0]?.id;
  let showEqual = false;
  const pairPages = new Map();
  const pairsPerPage = 25;
  let storageWorks = true;
  const $ = id => document.getElementById(id);
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  const canon = value => JSON.stringify(value, (_, v) => v && typeof v === "object" && !Array.isArray(v)
    ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
  const display = value => value == null || value === "" ? "—" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const message = text => { $("message").textContent = text; };
  const bodyOf = value => value && typeof value === "object" ? value.data ?? value : value;
  function contentKey(value, field) {
    const body = bodyOf(value);
    if (field === "tags") return canon(typeof value === "string" ? {tag:value,type:0} : {tag:value.tag,type:value.type??0});
    if (field === "notes") return canon({note:typeof body === "string" ? body : body.note??body,tags:typeof body === "object" ? body.tags??[] : []});
    if (field === "annotations" && body && typeof body === "object") {
      const aliases = new Set(["annotationType","type","annotationText","text","annotationComment","comment","annotationPageLabel","pageLabel","annotationPosition","position","annotationColor","color","annotationAuthorName","authorName"]);
      const identity = new Set(["key","id","itemID","parentItem","itemType","dateAdded","dateModified","version","libraryID"]);
      const extra = Object.fromEntries(Object.entries(body).filter(([key]) => !aliases.has(key) && !identity.has(key)));
      return canon({type:body.annotationType??body.type,text:body.annotationText??body.text,
        comment:body.annotationComment??body.comment,page:body.annotationPageLabel??body.pageLabel,
        position:body.annotationPosition??body.position,color:body.annotationColor??body.color,
        author:body.annotationAuthorName??body.authorName,extra});
    }
    return canon(value);
  }

  function validate(payload) {
    if (!payload || payload.schema_version !== 1 || payload.review_id !== review.review_id ||
        !payload.decisions || typeof payload.decisions !== "object" || Array.isArray(payload.decisions)) {
      throw Error("This decision file belongs to a different review snapshot or has an invalid format.");
    }
    const result = JSON.parse(JSON.stringify(data.decisions));
    for (const [id, decision] of Object.entries(payload.decisions)) {
      const pair = pairs.get(id);
      if (!pair || !decision || !["unreviewed", "same", "different", "defer"].includes(decision.status) ||
          (decision.preferred != null && ![pair.left, pair.right].includes(decision.preferred)) ||
          (decision.comment !== undefined && (typeof decision.comment !== "string" || decision.comment.length > 10000))) {
        throw Error("Invalid pair, status, preferred item or comment in decisions.");
      }
      result.decisions[id] = {status: decision.status, preferred: decision.preferred ?? null, comment: decision.comment ?? ""};
    }
    return result;
  }
  let saved = null;
  try { saved = localStorage.getItem(storageKey); }
  catch (_) { storageWorks = false; }
  if (saved) {
    try { state = validate(JSON.parse(saved)); }
    catch (_) { message("Saved browser decisions are invalid. Started with the unreviewed template; load a downloaded backup to recover."); }
  }
  function save(refresh = true) {
    try { localStorage.setItem(storageKey, JSON.stringify(state)); }
    catch (_) { storageWorks = false; }
    message(storageWorks ? "Decisions saved in this browser. Download a backup before closing." : "Browser storage is unavailable. Download decisions before closing this file.");
    if (refresh) { renderStats(); renderNavigation(); }
  }
  function renderStats() {
    const counts = {same: 0, different: 0, defer: 0, unreviewed: 0};
    Object.values(state.decisions).forEach(d => counts[d.status]++);
    const completed = counts.same + counts.different;
    $("progress").textContent = `${completed} of ${review.pairs.length} pairs reviewed`;
    $("counts").replaceChildren();
    for (const [number, label] of [[Object.keys(review.records).length, "source items"], [review.clusters.length, "candidate clusters"], [counts.same, "confirmed pairs"], [counts.unreviewed + counts.defer, "need review"]]) {
      const node = element("div", undefined, "count");
      node.append(element("b", number), element("span", label));
      $("counts").append(node);
    }
  }
  function filteredClusters() {
    const query = $("search").value.trim().toLocaleLowerCase();
    const filter = $("filter").value;
    return review.clusters.filter(cluster => {
      const clusterPairs = cluster.pairs.map(id => pairs.get(id));
      const matchesStatus = filter === "all" || (filter === "warning"
        ? clusterPairs.some(p => p.warnings.length)
        : clusterPairs.some(p => state.decisions[p.id].status === filter));
      const text = cluster.members.map(id => {
        const record = review.records[id];
        return [record.id, record.library, record.source_id, JSON.stringify(record.metadata)].join(" ");
      }).join(" ").concat(" ",cluster.pairs.join(" ")).toLocaleLowerCase();
      return matchesStatus && (!query || text.includes(query));
    });
  }
  function renderNavigation() {
    const clusters = filteredClusters();
    $("clusters").replaceChildren();
    for (const cluster of clusters) {
      const button = element("button", undefined, "cluster-button" + (cluster.id === selected ? " active" : ""));
      button.type = "button";
      button.setAttribute("aria-current", cluster.id === selected ? "true" : "false");
      const first = review.records[cluster.members[0]];
      const title = first.metadata.title || "Untitled source record";
      const count = cluster.pairs.filter(id => ["same", "different"].includes(state.decisions[id].status)).length;
      button.append(element("span", title, "cluster-title"), element("small", `${cluster.members.length} items · ${cluster.pairs.length} pairs · ${count} reviewed`));
      button.addEventListener("click", () => { selected = cluster.id; renderNavigation(); renderMain(); });
      $("clusters").append(button);
    }
    if (!clusters.length) $("clusters").append(element("p", "No clusters match this filter.", "muted"));
  }
  function contentList(record, other, field) {
    const root = element("div");
    root.append(element("div", record.library, "library"));
    const values = record[field];
    if (!values.length) { root.append(element("p", "No data in export", "muted")); return root; }
    const otherValues = new Set(other[field].map(value => contentKey(value,field)));
    const list = element("ul", undefined, "content-list");
    for (const [index,value] of values.entries()) {
      const shared = otherValues.has(contentKey(value,field));
      const li = element("li", undefined, shared ? "" : "unique");
      li.append(element("span", shared ? "Same content in both exports" : "Only in this export", "presence"));
      if (field === "tags") {
        li.append(element("span", typeof value === "string" ? value : `${value.tag ?? display(value)}${value.type === 1 ? " (automatic)" : ""}`));
      } else {
        const body = bodyOf(value);
        if (field === "annotations" && body && typeof body === "object") {
          const page = body.annotationPageLabel??body.pageLabel;
          const type = body.annotationType??body.type;
          li.append(element("p", [page != null && page !== "" ? `Page ${page}` : "Page not supplied",type].filter(Boolean).join(" · "),"muted"));
          for (const [label,text] of [["Highlight",body.annotationText??body.text],["Comment",body.annotationComment??body.comment]]) {
            li.append(element("h4",label),element("pre",text == null || text === "" ? "—" : text));
          }
        } else {
          const text = field === "notes" ? data.note_text?.[record.id]?.[index] ?? display(body) : display(body);
          li.append(element("pre",text));
        }
        const raw = element("details");
        raw.append(element("summary", "Exact exported content"), element("pre", display(value)));
        li.append(raw);
      }
      list.append(li);
    }
    root.append(list);
    return root;
  }
  function pairCard(pair, index) {
    const left = review.records[pair.left], right = review.records[pair.right];
    const decision = state.decisions[pair.id];
    const card = element("article", undefined, "pair");
    card.dataset.pairId = pair.id;
    const head = element("div", undefined, "pair-top");
    head.append(element("h3", `Pair ${index + 1} · ${left.library} ↔ ${right.library}`));
    head.append(element("div", pair.id, "source-id"));
    const evidence = element("div", undefined, "evidence");
    pair.reasons.forEach(reason => evidence.append(element("span", reason, "badge")));
    evidence.append(element("span", `Title similarity ${pair.title_similarity}%`, "badge"));
    head.append(evidence);
    pair.warnings.forEach(warning => head.append(element("p", warning, "warning")));
    card.append(head);
    const body = element("div", undefined, "pair-body");
    const sources = element("div", undefined, "sources");
    for (const record of [left, right]) {
      const source = element("section", undefined, "source");
      source.append(element("div", record.library, "library"), element("h3", record.metadata.title || "Untitled source record"), element("p", record.source_id, "source-id"), element("p", `Identity: ${record.identity_quality}`, "muted"));
      sources.append(source);
    }
    body.append(sources);
    const wrap = element("div", undefined, "table-wrap");
    const table = element("table");
    const thead = element("thead"), tr = element("tr");
    ["Field", left.library, right.library].forEach(label => tr.append(element("th", label)));
    thead.append(tr); table.append(thead);
    const tbody = element("tbody");
    let differences = 0;
    for (const key of [...new Set([...Object.keys(left.metadata), ...Object.keys(right.metadata)])].sort()) {
      const a = left.metadata[key], b = right.metadata[key];
      const different = canon(a ?? null) !== canon(b ?? null);
      if (!different && !showEqual) continue;
      if (different) differences++;
      const row = element("tr", undefined, different ? "different-field" : "");
      row.append(element("td", key), element("td", display(a)), element("td", display(b)));
      tbody.append(row);
    }
    table.append(tbody); wrap.append(table);
    if (!differences && !showEqual) wrap.append(element("p", "All normalized bibliographic fields agree.", "muted"));
    body.append(wrap);
    for (const [field, label] of [["tags", "Tags"], ["notes", "Notes"], ["annotations", "Annotations"], ["attachments", "Attachment metadata"]]) {
      const section = element("details", undefined, "collection");
      if (field === "tags" || field === "notes" || (field === "annotations" && (left[field].length || right[field].length))) section.open = true;
      section.append(element("summary", `${label} · ${left[field].length} / ${right[field].length}`));
      const columns = element("div", undefined, "sources");
      columns.append(contentList(left, right, field), contentList(right, left, field));
      section.append(columns); body.append(section);
    }
    const raw = element("details", undefined, "raw");
    raw.append(element("summary", "Original exported records (including unmapped fields)"));
    const columns = element("div", undefined, "sources");
    [left, right].forEach(record => { const column = element("div"); column.append(element("h4", record.library), element("pre", display(record.raw))); columns.append(column); });
    raw.append(columns); body.append(raw); card.append(body);
    const bar = element("div", undefined, "decision-bar");
    const statusLabel = element("label", "Your decision");
    const status = element("select"); status.setAttribute("aria-label", `Decision for ${pair.id}`);
    [["unreviewed", "Unreviewed"], ["same", "Same work — confirm correspondence"], ["different", "Different works"], ["defer", "Defer — need more evidence"]].forEach(([value, text]) => { const option = element("option", text); option.value = value; status.append(option); });
    status.value = decision.status;
    status.addEventListener("change", () => { decision.status = status.value; save(); });
    statusLabel.append(status); bar.append(statusLabel);
    const prefLabel = element("label", "Preferred metadata source (optional)");
    const preferred = element("select"); preferred.setAttribute("aria-label", `Preferred metadata for ${pair.id}`);
    [["", "Keep both variants for manual review"], [left.id, left.library], [right.id, right.library]].forEach(([value, text]) => { const option = element("option", text); option.value = value; preferred.append(option); });
    preferred.value = decision.preferred || "";
    preferred.addEventListener("change", () => { decision.preferred = preferred.value || null; save(false); });
    prefLabel.append(preferred); bar.append(prefLabel);
    const commentLabel = element("label", "Review note", "comment");
    const comment = element("textarea"); comment.maxLength = 10000; comment.value = decision.comment;
    comment.setAttribute("aria-label", `Review note for ${pair.id}`);
    comment.placeholder = "Why this is the same work, a version difference, or a false match…";
    comment.addEventListener("input", () => { decision.comment = comment.value; save(false); });
    commentLabel.append(comment); bar.append(commentLabel); card.append(bar);
    return card;
  }
  function renderMain() {
    const root = $("main"); root.replaceChildren();
    const cluster = review.clusters.find(cluster => cluster.id === selected);
    if (!cluster) {
      const empty = element("div", undefined, "empty-state");
      if (review.pairs.length) {
        empty.append(element("h2", "No candidates match this filter"), element("p", "Clear the search or change the review status to see other candidate clusters."));
      } else {
        empty.append(element("h2", "No cross-library candidates"), element("p", "The matching rules found no pairs in these exports. This does not establish that every item is unique. Check export completeness and consider a lower title threshold."));
      }
      root.append(empty); return;
    }
    const heading = element("div", undefined, "cluster-heading");
    const info = element("div");
    info.append(element("h2", "Review candidate cluster"), element("div", `${cluster.members.length} source items · ${cluster.pairs.length} pair decisions`, "muted"));
    const toggle = element("label", undefined, "toggle");
    const checkbox = element("input"); checkbox.type = "checkbox"; checkbox.checked = showEqual;
    checkbox.addEventListener("change", () => { showEqual = checkbox.checked; renderMain(); });
    toggle.append(checkbox, element("span", "Show equal fields")); heading.append(info, toggle); root.append(heading);
    const page = Math.max(0, Math.min(pairPages.get(cluster.id) || 0, Math.ceil(cluster.pairs.length / pairsPerPage) - 1));
    const start = page * pairsPerPage;
    if (cluster.pairs.length > pairsPerPage) {
      const pager = element("div", undefined, "pair-pager");
      pager.append(element("span", `Showing pairs ${start + 1}–${Math.min(start + pairsPerPage, cluster.pairs.length)} of ${cluster.pairs.length}`, "muted"));
      for (const [label, target] of [["Previous pairs", page - 1], ["Next pairs", page + 1]]) {
        const button = element("button", label, "secondary");
        button.disabled = target < 0 || target * pairsPerPage >= cluster.pairs.length;
        button.addEventListener("click", () => { pairPages.set(cluster.id, target); renderMain(); $("main").scrollIntoView?.({block:"start"}); });
        pager.append(button);
      }
      root.append(pager);
    }
    cluster.pairs.slice(start, start + pairsPerPage).forEach((id, index) => root.append(pairCard(pairs.get(id), start + index)));
    const nav = element("div", undefined, "nav-buttons");
    const visible = filteredClusters();
    const idx = visible.findIndex(c => c.id === selected);
    for (const [label, target] of [["Previous cluster", idx - 1], ["Next cluster", idx + 1]]) {
      if (target >= 0 && target < visible.length) {
        const button = element("button", label, "secondary");
        button.addEventListener("click", () => { selected = visible[target].id; renderNavigation(); renderMain(); $("main").scrollIntoView?.({block: "start"}); });
        nav.append(button);
      }
    }
    root.append(nav);
  }
  $("download").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(state, null, 2) + "\n"], {type: "application/json"});
    const url = URL.createObjectURL(blob);
    const link = element("a"); link.href = url; link.download = "decisions.json"; link.hidden = true;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message("Downloaded decisions.json. Use it with this review.json to export confirmed correspondences.");
  });
  $("import").addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > 16 * 1024 * 1024) throw Error("Decision file exceeds 16 MiB.");
      state = validate(JSON.parse(await file.text())); save(); renderMain(); message("Loaded decisions for this review snapshot.");
    } catch (error) { message(error.message); }
    event.target.value = "";
  });
  for (const id of ["search", "filter"]) $(id).addEventListener(id === "search" ? "input" : "change", () => {
    const visible = filteredClusters();
    if (!visible.some(c => c.id === selected)) selected = visible[0]?.id;
    renderNavigation(); renderMain();
  });
  for (const library of review.libraries) {
    const section = element("div", undefined, "warnings-list");
    section.append(element("h4", `${library.label} · ${library.records} items · ${library.file}`));
    library.warnings.forEach(warning => section.append(element("p", warning)));
    $("warnings").append(section);
  }
  if (!storageWorks) message("Browser storage is unavailable. Download decisions before closing this file.");
  renderStats(); renderNavigation(); renderMain();
})();

