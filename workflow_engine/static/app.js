"use strict";

const byId = (id) => document.getElementById(id);
const state = { users: [], workflows: [], definition: null, definitions: [], draft: [], draftSource: null, selected: null, events: [], actor: "submitter", busy: false, online: false, rejectStep: null, rework: null };
const eventNames = {
  workflow_created: "Assessment submitted", workflow_started: "Workflow started",
  step_activated: "Step activated", step_approved: "Step approved", step_rejected: "Step rejected",
  step_skipped: "Step skipped", workflow_completed: "Workflow completed",
  workflow_rejected: "Workflow rejected", workflow_cancelled: "Workflow cancelled",
  step_sent_back: "Step sent back", approval_forwarded: "Approval forwarded",
};

function element(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function userName(id) {
  return state.users.find((user) => user.id === id)?.name || id || "--";
}

function timestamp(value) {
  if (!value) return "--";
  const date = new Date(value.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`);
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function badge(status) {
  return element("span", `badge ${status.toLowerCase()}`, status);
}

function showNotice(message, isError = false) {
  byId("notice-text").textContent = message;
  byId("notice").classList.toggle("error", isError);
  byId("notice").hidden = false;
}

async function api(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Cannot reach the local API.");
  }
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`The API returned an unexpected response (${response.status}).`); }
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => `${item.loc.slice(1).join(".")}: ${item.msg}`).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return data;
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelector(".content").setAttribute("aria-busy", String(busy));
  byId("actor").disabled = busy || !state.users.length;
  byId("refresh").disabled = busy;
  byId("new-assessment").disabled = busy || !state.online;
  byId("empty-new").disabled = busy || !state.online;
  for (const id of ["create-submit", "reject-submit", "cancel-submit", "rework-submit"]) byId(id).disabled = busy;
  document.querySelectorAll("[data-close]").forEach((button) => { button.disabled = busy; });
  for (const id of ["saved-definition", "definition-name", "definition-version", "add-approval", "save-definition", "entity-id"]) byId(id).disabled = busy;
  byId("reviewer-fields").querySelectorAll("input, select").forEach((input) => { input.disabled = busy; });
  byId("reviewer-fields").querySelectorAll("button").forEach((button) => { button.disabled = busy || button.dataset.unavailable === "true"; });
  renderList();
  if (state.selected) renderAssessment();
}

function renderActor() {
  const user = state.users.find((item) => item.id === state.actor);
  byId("actor-avatar").textContent = user ? user.name.split(" ").map((part) => part[0]).join("").slice(0, 2) : "--";
  byId("actor").title = user?.role || "Mock user";
  byId("create-actor").textContent = userName(state.actor);
}

function renderList() {
  const query = byId("search").value.trim().toLowerCase();
  const filter = byId("status-filter").value;
  const workflows = state.workflows.filter((workflow) =>
    `${workflow.entity_id} ${workflow.id}`.toLowerCase().includes(query) && (filter === "all" || workflow.status === filter));
  byId("assessment-count").textContent = state.workflows.length;
  byId("list-empty").hidden = workflows.length > 0;
  byId("list-empty").textContent = state.workflows.length ? "No matching assessments" : "No assessments yet";
  byId("workflow-list").replaceChildren(...workflows.map((workflow) => {
    const item = element("li");
    const button = element("button", "workflow-item");
    button.type = "button";
    button.disabled = state.busy;
    button.setAttribute("aria-pressed", String(state.selected?.id === workflow.id));
    button.append(element("strong", null, workflow.entity_id), badge(workflow.status),
      element("span", "subtle", `Assessment #${workflow.id} / ${timestamp(workflow.created_at)} UTC`));
    button.addEventListener("click", () => selectWorkflow(workflow.id));
    item.append(button);
    return item;
  }));
}

function eventLabel(event) {
  const step = state.selected?.steps.find((item) => item.id === event.step_id);
  return `${eventNames[event.event_type] || event.event_type}${step ? ` / ${step.name}` : ""}`;
}

function renderAssessment() {
  const workflow = state.selected;
  byId("empty-state").hidden = Boolean(workflow);
  byId("assessment").hidden = !workflow;
  if (!workflow) return;
  const creator = state.events.find((event) => event.event_type === "workflow_created")?.actor_id;
  const terminal = ["Completed", "Rejected", "Cancelled"].includes(workflow.status);
  byId("assessment-id").textContent = `Assessment #${workflow.id} / ${workflow.entity_type}`;
  byId("application-id").textContent = workflow.entity_id;
  byId("workflow-status").textContent = workflow.status;
  byId("workflow-status").className = `badge ${workflow.status.toLowerCase()}`;
  byId("created-at").textContent = `${timestamp(workflow.created_at)} UTC`;
  byId("completed-at").textContent = workflow.completed_at ? `${timestamp(workflow.completed_at)} UTC` : "--";
  byId("submitter").textContent = userName(creator);
  byId("start").hidden = workflow.status !== "Pending";
  byId("start").disabled = state.busy || !state.online || state.actor !== creator;
  byId("start").title = state.actor === creator ? "Start the first approval" : `Only ${userName(creator)} can start this assessment`;
  byId("cancel").hidden = terminal;
  byId("cancel").disabled = state.busy || !state.online || state.actor !== creator;
  byId("cancel").title = state.actor === creator ? "Skip unfinished steps and cancel" : `Only ${userName(creator)} can cancel this assessment`;
  const approved = workflow.steps.filter((step) => step.status === "Approved").length;
  byId("progress").textContent = `${approved} of ${workflow.steps.length} approved`;
  byId("steps").replaceChildren(...workflow.steps.map((step, index) => {
    const item = element("li", `step ${step.status.toLowerCase()}`);
    item.style.setProperty("--delay", `${index * 55}ms`);
    const content = element("div");
    const top = element("div", "step-top");
    top.append(element("h3", null, step.name), badge(step.status));
    content.append(top, element("p", "assignee", `Assigned to ${userName(step.assigned_to)}`));
    if (step.original_assigned_to && step.original_assigned_to !== step.assigned_to) {
      content.append(element("p", "assignee", `Originally assigned to ${userName(step.original_assigned_to)}`));
    }
    if (step.status === "Active" && workflow.status === "Running") {
      const actions = element("div", "step-actions");
      const approve = element("button", "primary-button", "Approve");
      const reject = element("button", "quiet-button danger-text", "Reject");
      for (const button of [approve, reject]) {
        button.type = "button";
        button.disabled = state.busy || !state.online || step.assigned_to !== state.actor;
        button.title = step.assigned_to === state.actor ? `${button.textContent} ${step.name}` : `Only ${userName(step.assigned_to)} can act on this step`;
      }
      approve.addEventListener("click", () => mutate(`/workflows/${workflow.id}/steps/${step.id}/approve`, { user_id: state.actor }, "Step approved"));
      reject.addEventListener("click", () => {
        state.rejectStep = step.id;
        byId("reject-step").textContent = step.name;
        byId("reason").value = "";
        byId("reject-error").hidden = true;
        byId("reject-dialog").showModal();
      });
      actions.append(approve, reject);
      for (const [mode, label] of [["send-back", "Send back"], ["forward", "Forward"]]) {
        if (mode === "send-back" && !workflow.steps.some((target) => target.order < step.order && target.status === "Approved")) continue;
        const button = element("button", "quiet-button", label);
        button.type = "button";
        button.disabled = state.busy || !state.online || step.assigned_to !== state.actor;
        button.title = step.assigned_to === state.actor ? `${label} ${step.name}` : `Only ${userName(step.assigned_to)} can act on this step`;
        button.addEventListener("click", () => openRework(mode, step));
        actions.append(button);
      }
      content.append(actions);
    }
    item.append(element("span", "step-number", step.order), content);
    return item;
  }));
  byId("event-count").textContent = state.events.length;
  byId("events").replaceChildren(...state.events.map((event) => {
    const row = element("tr");
    const name = element("td", null, eventNames[event.event_type] || event.event_type);
    name.append(element("span", "event-id", `#${event.id}`));
    const step = workflow.steps.find((item) => item.id === event.step_id);
    let details = event.metadata.reason || (Object.keys(event.metadata).length ? JSON.stringify(event.metadata) : "--");
    if (event.event_type === "step_sent_back") {
      const target = workflow.steps.find((item) => item.id === event.metadata.target_step_id);
      details = `To ${target?.name || event.metadata.target_step_id}: ${event.metadata.reason}`;
    } else if (event.event_type === "approval_forwarded") {
      details = `${userName(event.metadata.from_user_id)} to ${userName(event.metadata.to_user_id)}: ${event.metadata.reason}`;
    }
    row.append(name, element("td", null, step?.name || "--"), element("td", null, userName(event.actor_id)),
      element("td", null, timestamp(event.created_at)), element("td", null, details));
    return row;
  }));
  const latest = state.events.at(-1);
  byId("latest-event").textContent = latest ? `${eventLabel(latest)} / ${userName(latest.actor_id)}` : "--";
  byId("latest-event-time").textContent = latest ? `${timestamp(latest.created_at)} UTC` : "";
}

async function loadSelected(id) {
  if (!id) {
    state.selected = null;
    state.events = [];
  } else {
    const [workflow, events] = await Promise.all([api(`/workflows/${id}`), api(`/workflows/${id}/events`)]);
    state.selected = workflow;
    state.events = events;
  }
  renderAssessment();
}

async function selectWorkflow(id) {
  if (state.busy) return;
  setBusy(true);
  try { await loadSelected(id); }
  catch (error) { showNotice(error.message, true); }
  finally { setBusy(false); }
}

async function loadDashboard(preferredId) {
  const [users, workflows, definition, definitions] = await Promise.all([api("/demo/users"), api("/workflows"), api("/workflow-definition"), api("/workflow-definitions")]);
  state.users = users;
  state.workflows = workflows;
  state.definition = definition;
  state.definitions = definitions;
  state.online = true;
  if (!users.some((user) => user.id === state.actor)) state.actor = users[0]?.id || "";
  byId("actor").replaceChildren(...users.map((user) => {
    const option = element("option", null, user.name);
    option.value = user.id;
    return option;
  }));
  byId("actor").value = state.actor;
  renderActor();
  const id = workflows.find((workflow) => workflow.id === preferredId)?.id || workflows[0]?.id;
  await loadSelected(id);
  byId("connection").textContent = "API connected";
  byId("connection").className = "connection connected";
}

async function refresh() {
  if (state.busy) return;
  setBusy(true);
  byId("notice").hidden = true;
  try { await loadDashboard(state.selected?.id); }
  catch (error) {
    state.online = false;
    byId("connection").textContent = "API unavailable";
    byId("connection").className = "connection unavailable";
    showNotice(error.message, true);
  } finally { setBusy(false); }
}

async function mutate(path, body, message, dialogId) {
  if (state.busy || !state.online) return;
  setBusy(true);
  let result;
  try { result = await api(path, body); }
  catch (error) {
    if (dialogId) {
      const errorId = dialogId.replace("-dialog", "-error");
      byId(errorId).textContent = error.message;
      byId(errorId).hidden = false;
    } else showNotice(error.message, true);
    setBusy(false);
    return;
  }
  if (dialogId) byId(dialogId).close();
  state.selected = result;
  try {
    await loadDashboard(result.id);
    showNotice(message);
  } catch {
    state.online = false;
    state.events = [];
    byId("connection").textContent = "Refresh required";
    byId("connection").className = "connection unavailable";
    renderAssessment();
    showNotice(`${message}. Refresh failed; refresh before the next action.`, true);
  } finally { setBusy(false); }
}

function openCreate() {
  if (!state.online || state.busy) return;
  byId("entity-id").value = `app-${String(state.workflows.length + 1).padStart(3, "0")}`;
  byId("definition-name").value = `Workflow ${byId("entity-id").value}`;
  byId("definition-version").value = 1;
  state.draftSource = null;
  state.draft = state.definition.steps.map((step) => {
    const role = step.name.toLowerCase().replace(/ approval$/, "");
    const defaultUser = state.users.find((user) => user.role.toLowerCase().startsWith(role));
    return { id: crypto.randomUUID(), name: step.name, assigned_to: defaultUser?.id || state.users[0].id };
  });
  renderSavedDefinitions();
  renderBuilder();
  byId("definition-saved").textContent = "";
  byId("create-error").hidden = true;
  renderActor();
  byId("create-dialog").showModal();
}

function renderSavedDefinitions(selected = "") {
  const fresh = element("option", null, "New workflow");
  fresh.value = "";
  byId("saved-definition").replaceChildren(fresh, ...state.definitions.map((definition) => {
    const option = element("option", null, `${definition.name} / v${definition.version} / ${definition.steps.length} approvals`);
    option.value = definition.id;
    return option;
  }));
  byId("saved-definition").value = selected;
}

function markDraftChanged() {
  byId("definition-saved").textContent = "";
  if (state.draftSource && Number(byId("definition-version").value) === state.draftSource.version) {
    byId("definition-version").value = state.draftSource.version + 1;
  }
}

function renderBuilder() {
  byId("reviewer-fields").replaceChildren(...state.draft.map((step, index) => {
    const row = element("li", "builder-row");
    row.dataset.stepId = step.id;
    const heading = element("div", "builder-row-heading");
    heading.append(element("strong", null, `Approval ${index + 1}`));
    const tools = element("div", "builder-tools");
    for (const [action, symbol, label, unavailable] of [
      ["up", "\u2191", "Move approval up", index === 0],
      ["down", "\u2193", "Move approval down", index === state.draft.length - 1],
      ["remove", "\u00d7", "Remove approval", state.draft.length === 1],
    ]) {
      const button = element("button", "quiet-button", symbol);
      button.type = "button";
      button.title = label;
      button.setAttribute("aria-label", `${label} ${index + 1}`);
      button.dataset.unavailable = String(unavailable);
      button.disabled = unavailable || state.busy;
      button.addEventListener("click", () => {
        if (state.busy) return;
        if (action === "remove") state.draft.splice(index, 1);
        else {
          const destination = index + (action === "up" ? -1 : 1);
          [state.draft[index], state.draft[destination]] = [state.draft[destination], state.draft[index]];
        }
        markDraftChanged();
        renderBuilder();
      });
      tools.append(button);
    }
    heading.append(tools);
    const fields = element("div", "builder-inputs");
    const nameLabel = element("label", null, "Step name");
    const name = element("input");
    name.required = true;
    name.pattern = ".*\\S.*";
    name.value = step.name;
    name.id = `approval-name-${index}`;
    nameLabel.htmlFor = name.id;
    name.addEventListener("input", () => { step.name = name.value; markDraftChanged(); });
    nameLabel.append(name);
    const approverLabel = element("label", null, "Approver");
    const approver = element("select");
    approver.required = true;
    approver.id = `reviewer-${index}`;
    approverLabel.htmlFor = approver.id;
    approver.replaceChildren(...state.users.map((user) => {
      const option = element("option", null, `${user.name} / ${user.role}`);
      option.value = user.id;
      return option;
    }));
    approver.value = step.assigned_to;
    approver.addEventListener("change", () => { step.assigned_to = approver.value; markDraftChanged(); });
    approverLabel.append(approver);
    fields.append(nameLabel, approverLabel);
    row.append(heading, fields);
    return row;
  }));
}

async function saveBuilderDefinition() {
  if (state.busy || !state.online) return null;
  const inputs = [byId("definition-name"), byId("definition-version"), ...byId("reviewer-fields").querySelectorAll("input, select")];
  for (const input of inputs) if (!input.reportValidity()) return null;
  const payload = {
    name: byId("definition-name").value.trim(), version: Number(byId("definition-version").value),
    steps: state.draft.map((step, index) => ({ ...step, name: step.name.trim(), order: index + 1 })),
  };
  setBusy(true);
  byId("create-error").hidden = true;
  try {
    const saved = await api("/workflow-definitions", payload);
    state.definitions = [saved, ...state.definitions.filter((definition) => definition.id !== saved.id)];
    state.draftSource = saved;
    renderSavedDefinitions(String(saved.id));
    byId("definition-saved").textContent = `Saved / v${saved.version}`;
    return saved;
  } catch (error) {
    byId("create-error").textContent = error.message;
    byId("create-error").hidden = false;
    return null;
  } finally { setBusy(false); }
}

function openRework(mode, step) {
  state.rework = { mode, stepId: step.id };
  const sendBack = mode === "send-back";
  byId("rework-title").textContent = sendBack ? "Send step back" : "Forward approval";
  byId("rework-submit").textContent = sendBack ? "Send back" : "Forward approval";
  byId("rework-target-label").textContent = sendBack ? "Previous step" : "New reviewer";
  byId("rework-step").textContent = step.name;
  const choices = sendBack
    ? state.selected.steps.filter((target) => target.order < step.order && target.status === "Approved").map((target) => ({ id: target.id, name: target.name }))
    : state.users.filter((user) => user.id !== step.assigned_to);
  byId("rework-target").replaceChildren(...choices.map((choice) => {
    const option = element("option", null, choice.name);
    option.value = choice.id;
    return option;
  }));
  byId("rework-reason").value = "";
  byId("rework-error").hidden = true;
  byId("rework-dialog").showModal();
}

function selectTab(id) {
  for (const tab of ["overview", "audit"]) {
    const selected = id === tab;
    byId(`${tab}-tab`).setAttribute("aria-selected", String(selected));
    byId(`${tab}-tab`).tabIndex = selected ? 0 : -1;
    byId(`${tab}-panel`).hidden = !selected;
  }
}

byId("actor").addEventListener("change", (event) => { state.actor = event.target.value; renderActor(); renderAssessment(); });
byId("search").addEventListener("input", renderList);
byId("status-filter").addEventListener("change", renderList);
byId("refresh").addEventListener("click", refresh);
byId("dismiss-notice").addEventListener("click", () => { byId("notice").hidden = true; });
for (const id of ["new-assessment", "empty-new"]) byId(id).addEventListener("click", openCreate);
document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => byId(button.dataset.close).close()));
document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("cancel", (event) => { if (state.busy) event.preventDefault(); }));
byId("start").addEventListener("click", () => mutate(`/workflows/${state.selected.id}/start`, { user_id: state.actor }, "Workflow started"));
byId("cancel").addEventListener("click", () => {
  byId("cancel-target").textContent = state.selected.entity_id;
  byId("cancel-error").hidden = true;
  byId("cancel-dialog").showModal();
});
byId("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const saved = await saveBuilderDefinition();
  if (!saved) return;
  mutate(`/workflow-definitions/${saved.id}/workflows`, {
    entity_type: "application", entity_id: byId("entity-id").value.trim(), user_id: state.actor,
  }, "Assessment created", "create-dialog");
});
byId("add-approval").addEventListener("click", () => {
  if (state.busy) return;
  state.draft.push({ id: crypto.randomUUID(), name: "", assigned_to: state.users[0].id });
  markDraftChanged();
  renderBuilder();
  byId("reviewer-fields").lastElementChild.querySelector("input").focus();
});
byId("save-definition").addEventListener("click", saveBuilderDefinition);
byId("definition-name").addEventListener("input", markDraftChanged);
byId("saved-definition").addEventListener("change", () => {
  const saved = state.definitions.find((definition) => definition.id === Number(byId("saved-definition").value));
  if (!saved) { openCreate(); return; }
  state.draftSource = saved;
  state.draft = saved.steps.map(({ id, name, assigned_to }) => ({ id, name, assigned_to }));
  byId("definition-name").value = saved.name;
  byId("definition-version").value = saved.version;
  byId("definition-saved").textContent = `Saved / v${saved.version}`;
  byId("create-error").hidden = true;
  renderBuilder();
});
byId("reject-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const reason = byId("reason").value.trim();
  if (!reason) { byId("reject-error").textContent = "A rejection reason is required."; byId("reject-error").hidden = false; return; }
  mutate(`/workflows/${state.selected.id}/steps/${state.rejectStep}/reject`, { user_id: state.actor, reason }, "Assessment rejected", "reject-dialog");
});
byId("cancel-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(`/workflows/${state.selected.id}/cancel`, { user_id: state.actor }, "Assessment cancelled", "cancel-dialog");
});
byId("rework-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const reason = byId("rework-reason").value.trim();
  if (!reason) { byId("rework-error").textContent = "A reason is required."; byId("rework-error").hidden = false; return; }
  const body = { user_id: state.actor, reason };
  if (state.rework.mode === "send-back") body.target_step_id = Number(byId("rework-target").value);
  else body.to_user_id = byId("rework-target").value;
  mutate(`/workflows/${state.selected.id}/steps/${state.rework.stepId}/${state.rework.mode}`, body,
    state.rework.mode === "send-back" ? "Step sent back" : "Approval forwarded", "rework-dialog");
});
for (const tab of ["overview", "audit"]) byId(`${tab}-tab`).addEventListener("click", () => selectTab(tab));
document.querySelector(".tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tab = event.key === "Home" ? "overview" : event.key === "End" ? "audit" : byId("overview-tab").getAttribute("aria-selected") === "true" ? "audit" : "overview";
  selectTab(tab);
  byId(`${tab}-tab`).focus();
});
refresh();