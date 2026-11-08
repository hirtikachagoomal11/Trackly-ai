/* Trackly browser interactions. Persistent state and recommendations come from Flask. */
"use strict";

const API = "/api";
const statuses = ["Applied", "Interview", "Offer", "Rejected"];
const priorities = ["Low", "Medium", "High"];
const kanbanStatuses = ["To Do", "In Progress", "Completed"];
const roles = ["Data Analyst", "Python Developer", "Frontend Developer", "Full Stack Developer", "AI Developer"];
let projects = [];
let applications = [];
let currentView = "month";
let periodDate = new Date();
let calendarMode = "month";
let calendarDate = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let calendarEvents = [];
let editingProjectId = null;
let activeProjectId = null;
let editingTaskId = null;
let editingApplicationId = null;
let applicationChart = null;
let taskChart = null;
let weeklyChart = null;
let monthlyChart = null;

const byId = id => document.getElementById(id);
const ownerCsrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
const localDateKey = date => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
};

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-CSRFToken": ownerCsrfToken(),
    ...(options.headers || {})
  };
  const response = await fetch(`${API}${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `Request failed (${response.status}).`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function showToast(message, isError = false) {
  const toast = document.createElement("div");
  toast.className = `toast${isError ? " error" : ""}`;
  toast.textContent = message;
  byId("toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 3500);
}

async function runAction(action, successMessage) {
  try {
    await action();
    if (successMessage) showToast(successMessage);
    await refreshDashboard();
  } catch (error) {
    showToast(error.message, true);
  }
}

function formatDate(value, options = { month: "short", day: "numeric", year: "numeric" }) {
  if (!value) return "Not set";
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(undefined, options);
}

function getPlannerRange() {
  const start = new Date(periodDate);
  const end = new Date(periodDate);
  if (currentView === "week") {
    start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    end.setTime(start.getTime());
    end.setDate(end.getDate() + 6);
  } else if (currentView === "month") {
    start.setDate(1);
    end.setMonth(end.getMonth() + 1, 0);
  }
  return { start: localDateKey(start), end: localDateKey(end) };
}

function updatePlannerLabel() {
  if (currentView === "day") {
    byId("period-label").textContent = periodDate.toLocaleDateString(undefined, {
      weekday: "short", month: "short", day: "numeric"
    });
  } else if (currentView === "week") {
    const range = getPlannerRange();
    byId("period-label").textContent =
      `${formatDate(range.start, { month: "short", day: "numeric" })} – ${formatDate(range.end, { month: "short", day: "numeric" })}`;
  } else {
    byId("period-label").textContent =
      periodDate.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  }
  document.querySelectorAll("[data-view]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.view === currentView));
  });
}

function plannerTaskIsVisible(task) {
  if (!task.dueDate) return true;
  const range = getPlannerRange();
  return task.dueDate >= range.start && task.dueDate <= range.end;
}

function priorityBadge(priority, suffix = "") {
  const badge = document.createElement("span");
  badge.className = `priority-badge priority-${priority.toLowerCase()}`;
  badge.textContent = `${priority}${suffix}`;
  return badge;
}

function actionButton(text, label, action, className = "small-button") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", action);
  return button;
}

function renderTask(project, task) {
  const row = document.createElement("li");
  row.className = "task-row";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = task.status === "Completed";
  checkbox.setAttribute("aria-label", `Mark ${task.title} ${checkbox.checked ? "incomplete" : "complete"}`);
  checkbox.addEventListener("change", () => runAction(
    () => api(`/tasks/${task.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: checkbox.checked ? "Completed" : "To Do" })
    }),
    checkbox.checked ? "Task completed — nice work!" : "Task marked as in progress."
  ));
  const title = document.createElement("span");
  title.className = "task-name";
  title.textContent = task.title;
  const due = document.createElement("span");
  due.className = "date-badge";
  due.textContent = task.dueDate ? formatDate(task.dueDate, { month: "short", day: "numeric" }) : "Unscheduled";
  const actions = document.createElement("span");
  actions.className = "task-row-actions";
  actions.append(
    actionButton("Edit", `Edit ${task.title}`, () => openTaskDialog(project.id, task)),
    actionButton("×", `Delete ${task.title}`, () =>
      runAction(() => api(`/tasks/${task.id}`, { method: "DELETE" }), "Task deleted."),
    "small-button danger")
  );
  row.append(checkbox, title, due, priorityBadge(task.priority), actions);
  return row;
}

function renderProjects() {
  const root = byId("planner-projects");
  root.replaceChildren();
  if (!projects.length) {
    const empty = document.createElement("div");
    empty.className = "empty-projects";
    empty.textContent = "No projects yet. Create a project to start planning ✨";
    root.append(empty);
    updateSummary();
    return;
  }
  projects.forEach(project => {
    const card = document.createElement("article");
    card.className = "card project-card";
    const head = document.createElement("div");
    head.className = "project-head";
    const titleWrap = document.createElement("div");
    titleWrap.className = "project-title-wrap";
    const icon = document.createElement("span");
    icon.className = "project-icon";
    icon.textContent = project.name.toLowerCase().includes("data") ? "📊" : "✨";
    const titleStack = document.createElement("div");
    const title = document.createElement("h3");
    title.className = "project-title";
    title.textContent = project.name;
    const dates = document.createElement("div");
    dates.className = "project-dates";
    dates.textContent = `Starts ${formatDate(project.startDate)} · Deadline ${formatDate(project.deadline)}`;
    titleStack.append(title, dates);
    titleWrap.append(icon, titleStack);
    const actions = document.createElement("div");
    actions.className = "project-actions";
    actions.append(
      actionButton("＋ Task", `Add task to ${project.name}`, () => openTaskDialog(project.id)),
      actionButton("Edit", `Edit ${project.name}`, () => openProjectDialog(project)),
      actionButton("Delete", `Delete ${project.name}`, () =>
        runAction(() => api(`/projects/${project.id}`, { method: "DELETE" }), "Project and its tasks deleted."),
      "small-button danger")
    );
    head.append(titleWrap, actions);
    const meta = document.createElement("div");
    meta.className = "project-meta";
    meta.append(priorityBadge(project.priority, " priority"));
    const progressLabel = document.createElement("div");
    progressLabel.className = "project-progress-label";
    progressLabel.textContent = `${project.completedTasks} of ${project.taskCount} tasks complete`;
    const progress = document.createElement("div");
    progress.className = "progress-line";
    const fill = document.createElement("span");
    fill.style.width = `${project.progress}%`;
    progress.append(fill);
    const list = document.createElement("ul");
    list.className = "task-list";
    const visibleTasks = project.tasks.filter(plannerTaskIsVisible).sort((a, b) => {
      if (!a.dueDate) return b.dueDate ? 1 : 0;
      if (!b.dueDate) return -1;
      return a.dueDate.localeCompare(b.dueDate);
    });
    visibleTasks.forEach(task => list.append(renderTask(project, task)));
    if (!visibleTasks.length) {
      const empty = document.createElement("li");
      empty.className = "project-empty";
      empty.textContent = "No tasks planned for this view. Add one or choose another date range.";
      list.append(empty);
    }
    card.append(head, meta, progressLabel, progress, list);
    root.append(card);
  });
  updateSummary();
}

function updateSummary() {
  const taskCount = projects.reduce((total, project) => total + project.taskCount, 0);
  const completed = projects.reduce((total, project) => total + project.completedTasks, 0);
  const percent = taskCount ? Math.round(completed / taskCount * 100) : 0;
  byId("overall-percent").textContent = `${percent}%`;
  byId("overall-ring").style.setProperty("--progress", `${percent * 3.6}deg`);
  byId("overall-count").textContent = `${completed} of ${taskCount} tasks complete`;
  byId("project-summary").textContent = `${projects.length} projects · ${taskCount} tasks`;
  byId("stat-projects").textContent = projects.length;
  byId("stat-tasks").textContent = `${taskCount} tasks across your projects`;
  byId("stat-completed").textContent = completed;
  byId("stat-remaining").textContent = `${taskCount - completed} left to tackle`;
  byId("stat-applications").textContent = applications.length;
  const interviews = applications.filter(item => item.status === "Interview").length;
  const offers = applications.filter(item => item.status === "Offer").length;
  byId("stat-interviews").textContent = `${interviews} interviews in progress`;
  byId("stat-offers").textContent = offers;
  byId("summary-interviews").textContent = interviews;
  byId("summary-offers").textContent = offers;
  byId("summary-applications").textContent = applications.length;
}

function renderKanban() {
  const root = byId("kanban-board");
  root.replaceChildren();
  const allTasks = projects.flatMap(project => project.tasks.map(task => ({ ...task, project })));
  kanbanStatuses.forEach(status => {
    const column = document.createElement("section");
    column.className = "kanban-column";
    column.dataset.status = status;
    const matching = allTasks.filter(task => task.status === status);
    const header = document.createElement("div");
    header.className = "kanban-column-head";
    const name = document.createElement("span");
    name.textContent = status;
    const count = document.createElement("span");
    count.className = "kanban-count";
    count.textContent = matching.length;
    header.append(name, count);
    const cards = document.createElement("div");
    cards.className = "kanban-cards";
    cards.dataset.status = status;
    cards.addEventListener("dragover", event => {
      event.preventDefault();
      column.classList.add("drag-over");
    });
    cards.addEventListener("dragleave", event => {
      if (!cards.contains(event.relatedTarget)) column.classList.remove("drag-over");
    });
    cards.addEventListener("drop", event => {
      event.preventDefault();
      column.classList.remove("drag-over");
      const taskId = Number(event.dataTransfer.getData("text/plain"));
      const source = allTasks.find(task => task.id === taskId);
      if (source && source.status !== status) {
        runAction(
          () => api(`/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify({ status }) }),
          `Task moved to ${status}.`
        );
      }
    });
    if (!matching.length) {
      const empty = document.createElement("p");
      empty.className = "project-empty";
      empty.textContent = "Drop a task here";
      cards.append(empty);
    }
    matching.forEach(task => {
      const card = document.createElement("article");
      card.className = "kanban-task";
      card.draggable = true;
      card.addEventListener("dragstart", event => {
        event.dataTransfer.setData("text/plain", String(task.id));
        event.dataTransfer.effectAllowed = "move";
        card.classList.add("dragging");
      });
      card.addEventListener("dragend", () => card.classList.remove("dragging"));
      const title = document.createElement("h4");
      title.className = "kanban-task-title";
      title.textContent = task.title;
      const metadata = document.createElement("div");
      metadata.className = "kanban-task-meta";
      const projectName = document.createElement("span");
      projectName.textContent = task.project.name;
      const dueDate = document.createElement("span");
      dueDate.textContent = task.dueDate ? `· ${formatDate(task.dueDate, { month: "short", day: "numeric" })}` : "· No due date";
      metadata.append(projectName, dueDate, priorityBadge(task.priority));
      const actions = document.createElement("div");
      actions.className = "kanban-task-actions";
      actions.append(actionButton("Edit", `Edit ${task.title}`, () => openTaskDialog(task.project.id, task)));
      card.append(title, metadata, actions);
      cards.append(card);
    });
    column.append(header, cards);
    root.append(column);
  });
  if (!allTasks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-projects";
    empty.textContent = "Create a project and add tasks to populate your board.";
    root.append(empty);
  }
}

function renderApplications() {
  const tbody = byId("application-rows");
  tbody.replaceChildren();
  byId("application-empty").hidden = applications.length > 0;
  applications.forEach(application => {
    const row = document.createElement("tr");
    const company = document.createElement("td");
    company.className = "company-cell";
    company.textContent = application.company;
    const role = document.createElement("td");
    role.className = "role-cell";
    role.textContent = application.role;
    const date = document.createElement("td");
    date.textContent = formatDate(application.date);
    const statusCell = document.createElement("td");
    const status = document.createElement("select");
    status.className = `status status-${application.status.toLowerCase()}`;
    status.setAttribute("aria-label", `Status for ${application.company}`);
    statuses.forEach(value => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      option.selected = value === application.status;
      status.append(option);
    });
    status.addEventListener("change", () => runAction(
      () => api(`/applications/${application.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: status.value })
      }),
      `Application status updated to ${status.value}.`
    ));
    statusCell.append(status);
    const actions = document.createElement("td");
    actions.append(
      actionButton("Edit", `Edit ${application.company} application`, () => openApplicationDialog(application)),
      actionButton("×", `Delete ${application.company} application`, () =>
        runAction(() => api(`/applications/${application.id}`, { method: "DELETE" }), "Application deleted."),
      "delete-button")
    );
    row.append(company, role, date, statusCell, actions);
    tbody.append(row);
  });
}

function renderCharts(analytics) {
  window.currentAnalytics = analytics;
  if (typeof Chart === "undefined" || !analytics) return;
  const dark = document.documentElement.dataset.theme === "dark";
  const color = dark ? "#aaa7c2" : "#858298";
  const gridColor = dark ? "#ffffff14" : "#e9e7f0";
  const statusCounts = statuses.map(status => analytics.applicationStatuses[status] || 0);
  const taskValues = [analytics.taskProgress.completed, analytics.taskProgress.remaining];
  const commonOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: "bottom", labels: { color, usePointStyle: true, pointStyle: "circle", boxWidth: 7, padding: 14, font: { size: 9 } } }
    }
  };
  if (applicationChart) applicationChart.destroy();
  applicationChart = new Chart(byId("application-chart"), {
    type: "doughnut",
    data: { labels: statuses, datasets: [{ data: statusCounts, backgroundColor: ["#7657f6", "#ffb84d", "#28b889", "#f05b78"], borderColor: dark ? "#242238" : "#fff", borderWidth: 3, hoverOffset: 7 }] },
    options: { ...commonOptions, cutout: "68%" }
  });
  if (taskChart) taskChart.destroy();
  taskChart = new Chart(byId("task-chart"), {
    type: "bar",
    data: { labels: ["Completed", "Remaining"], datasets: [{ data: taskValues, backgroundColor: ["#7657f6", "#f3a5cc"], borderRadius: 8, maxBarThickness: 54 }] },
    options: {
      responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false }, ticks: { color, font: { size: 9 } } }, y: { beginAtZero: true, ticks: { color, precision: 0, font: { size: 9 } }, grid: { color: gridColor } } }
    }
  });
  if (weeklyChart) weeklyChart.destroy();
  weeklyChart = new Chart(byId("weekly-chart"), {
    type: "line",
    data: { labels: analytics.weeklyCompletions.map(item => formatDate(item.week, { month: "short", day: "numeric" })), datasets: [{ label: "Completed", data: analytics.weeklyCompletions.map(item => item.completed), borderColor: "#e05aa9", backgroundColor: "#f05ba833", fill: true, tension: .35 }] },
    options: { ...commonOptions, scales: { x: { ticks: { color, font: { size: 8 } }, grid: { display: false } }, y: { beginAtZero: true, ticks: { color, precision: 0 }, grid: { color: gridColor } } } }
  });
  if (monthlyChart) monthlyChart.destroy();
  monthlyChart = new Chart(byId("monthly-chart"), {
    type: "bar",
    data: { labels: analytics.monthlyCompletions.map(item => formatDate(`${item.month}-01`, { month: "short", year: "2-digit" })), datasets: [{ label: "Completed", data: analytics.monthlyCompletions.map(item => item.completed), backgroundColor: ["#7657f6", "#9273f5", "#b28bed", "#ed75ae", "#ff9a54", "#49b5c5"], borderRadius: 7 }] },
    options: { ...commonOptions, plugins: { legend: { display: false } }, scales: { x: { ticks: { color, font: { size: 8 } }, grid: { display: false } }, y: { beginAtZero: true, ticks: { color, precision: 0 }, grid: { color: gridColor } } } }
  });
  const summary = byId("analytics-summary");
  summary.replaceChildren();
  const overdue = document.createElement("span");
  overdue.className = "date-badge";
  overdue.textContent = `${analytics.overdueTasks} overdue tasks`;
  const projectsSummary = document.createElement("span");
  projectsSummary.className = "date-badge";
  projectsSummary.textContent = `${analytics.projectProgress.length} project${analytics.projectProgress.length === 1 ? "" : "s"} tracked`;
  summary.append(overdue, projectsSummary);
}

function renderCalendar() {
  const grid = byId("calendar-grid");
  grid.replaceChildren();
  const label = byId("calendar-label");
  const rangeStart = new Date(calendarDate);
  const rangeEnd = new Date(calendarDate);
  if (calendarMode === "month") {
    label.textContent = calendarDate.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    rangeStart.setDate(1);
    rangeStart.setDate(rangeStart.getDate() - ((rangeStart.getDay() + 6) % 7));
    rangeEnd.setTime(rangeStart.getTime());
    rangeEnd.setDate(rangeEnd.getDate() + 41);
  } else {
    const shift = (rangeStart.getDay() + 6) % 7;
    rangeStart.setDate(rangeStart.getDate() - shift);
    rangeEnd.setTime(rangeStart.getTime());
    rangeEnd.setDate(rangeEnd.getDate() + 6);
    label.textContent = `${formatDate(localDateKey(rangeStart), { month: "short", day: "numeric" })} – ${formatDate(localDateKey(rangeEnd), { month: "short", day: "numeric", year: "numeric" })}`;
  }
  const from = localDateKey(rangeStart);
  const to = localDateKey(rangeEnd);
  api(`/calendar?start=${encodeURIComponent(from)}&end=${encodeURIComponent(to)}`)
    .then(events => {
      calendarEvents = events;
      drawCalendarGrid(grid, rangeStart, rangeEnd);
    })
    .catch(error => showToast(error.message, true));
  document.querySelectorAll("[data-calendar-view]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.calendarView === calendarMode));
  });
}

function drawCalendarGrid(grid, rangeStart, rangeEnd) {
  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach(day => {
    const header = document.createElement("div");
    header.className = "calendar-weekday";
    header.textContent = day;
    grid.append(header);
  });
  const today = localDateKey(new Date());
  const visibleMonth = calendarDate.getMonth();
  const totalDays = Math.round((rangeEnd - rangeStart) / 86400000) + 1;
  for (let offset = 0; offset < totalDays; offset++) {
    const date = new Date(rangeStart);
    date.setDate(date.getDate() + offset);
    const key = localDateKey(date);
    const cell = document.createElement("div");
    cell.className = "calendar-day";
    if (calendarMode === "month" && date.getMonth() !== visibleMonth) cell.classList.add("outside-month");
    if (key === today) cell.classList.add("today");
    const dateButton = document.createElement("button");
    dateButton.className = "calendar-date";
    dateButton.type = "button";
    dateButton.textContent = date.getDate();
    dateButton.setAttribute("aria-label", `Create a task on ${formatDate(key)}`);
    dateButton.addEventListener("click", () => {
      const project = projects[0];
      if (!project) return showToast("Create a project before adding a task.", true);
      openTaskDialog(project.id, null, key);
    });
    const eventsRoot = document.createElement("div");
    eventsRoot.className = "calendar-events";
    calendarEvents.filter(event => event.date === key).forEach(event => {
      const eventButton = document.createElement("button");
      eventButton.type = "button";
      eventButton.className = `calendar-event type-${event.type}${event.status === "Completed" ? " is-completed" : ""}`;
      eventButton.textContent = event.title;
      eventButton.title = `${event.type}: ${event.title}`;
      eventButton.addEventListener("click", () => handleCalendarEvent(event));
      eventsRoot.append(eventButton);
    });
    cell.append(dateButton, eventsRoot);
    grid.append(cell);
  }
}

function handleCalendarEvent(event) {
  if (event.type === "task") {
    const project = projects.find(item => item.id === event.projectId);
    const task = project?.tasks.find(item => item.id === event.entityId);
    if (project && task) openTaskDialog(project.id, task);
  } else if (event.type === "milestone") {
    const next = event.status === "Completed" ? "Upcoming" : "Completed";
    runAction(
      () => api(`/milestones/${event.entityId}`, { method: "PATCH", body: JSON.stringify({ status: next }) }),
      `Milestone marked ${next.toLowerCase()}.`
    );
  } else if (event.type === "interview") {
    const application = applications.find(item => item.id === event.entityId);
    if (application) openApplicationDialog(application);
  }
}

async function loadNotifications() {
  try {
    const data = await api("/notifications");
    byId("notification-count").textContent = data.unreadCount;
    byId("notification-count").hidden = data.unreadCount === 0;
    const root = byId("notification-list");
    root.replaceChildren();
    if (!data.notifications.length) {
      const empty = document.createElement("p");
      empty.className = "project-empty";
      empty.textContent = "You’re all caught up. Upcoming reminders will appear here.";
      root.append(empty);
      return;
    }
    data.notifications.forEach(notification => {
      const item = document.createElement("article");
      item.className = `notification-item${notification.isRead ? "" : " unread"}`;
      const content = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = notification.title;
      const due = document.createElement("small");
      due.textContent = `${notification.kind} · ${formatDate(notification.dueDate)}`;
      content.append(title, due);
      item.append(content);
      if (!notification.isRead) {
        item.append(actionButton("Mark read", `Mark ${notification.title} as read`, () =>
          runAction(() => api(`/notifications/${notification.id}`, { method: "PATCH", body: "{}" }), "Reminder marked as read."),
        "small-button"));
      }
      root.append(item);
    });
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderCareerAnalysis(data) {
  const root = byId("career-analysis");
  root.replaceChildren();
  const score = document.createElement("div");
  score.className = "career-score";
  const percent = document.createElement("strong");
  percent.textContent = `${data.estimatedMatchPercent}%`;
  const caption = document.createElement("span");
  caption.textContent = `${data.targetRole} · heuristic skill coverage`;
  score.append(percent, caption);
  const disclaimer = document.createElement("p");
  disclaimer.className = "chart-subtitle";
  disclaimer.textContent = data.estimateDisclaimer;
  root.append(score, disclaimer);
  function addList(title, values) {
    const heading = document.createElement("strong");
    heading.textContent = title;
    root.append(heading);
    if (!values.length) {
      const empty = document.createElement("p");
      empty.className = "career-placeholder";
      empty.textContent = "None identified from the text provided.";
      root.append(empty);
      return;
    }
    const list = document.createElement("ul");
    list.className = "career-list";
    values.forEach(value => {
      const item = document.createElement("li");
      item.textContent = typeof value === "string" ? value : `${value.skill}: ${value.activity}`;
      list.append(item);
    });
    root.append(list);
  }
  addList("Matching skills", data.matchingSkills);
  addList("Potential gaps", data.potentialGaps);
  addList("Learning roadmap", data.learningRoadmap);
  addList("Portfolio ideas", data.portfolioSuggestions);
  const method = document.createElement("p");
  method.className = "chart-subtitle";
  method.textContent = data.method;
  root.append(method);
  renderInterviewQuestions(data.targetRole);
}

async function renderInterviewQuestions(role) {
  try {
    const data = await api(`/career/interview-questions?role=${encodeURIComponent(role)}`);
    const root = byId("interview-questions");
    root.replaceChildren();
    const list = document.createElement("ol");
    list.className = "career-list";
    data.questions.forEach(question => {
      const item = document.createElement("li");
      item.textContent = question;
      list.append(item);
    });
    root.append(list);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadCareerProfile() {
  try {
    const profile = await api("/career/profile");
    byId("career-role").value = profile.targetRole || "Data Analyst";
    byId("career-experience").value = profile.experienceLevel || "Beginner";
    byId("career-skills").value = profile.skills;
    byId("career-goals").value = profile.careerGoals;
    byId("career-improve").value = profile.improveSkills;
    await renderInterviewQuestions(byId("career-role").value);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function refreshDashboard() {
  const [dashboard, applicationData] = await Promise.all([
    api("/dashboard"),
    api("/applications")
  ]);
  projects = dashboard.projects;
  applications = applicationData;
  window.currentAnalytics = dashboard.analytics;
  renderProjects();
  renderKanban();
  renderApplications();
  updateSummary();
  renderCharts(dashboard.analytics);
  renderCalendar();
  await loadNotifications();
}

function openDialog(dialogId) {
  byId(dialogId).showModal();
}

function openProjectDialog(project = null) {
  editingProjectId = project?.id ?? null;
  const form = byId("project-form");
  form.reset();
  byId("project-dialog-title").textContent = project ? "Edit project ✨" : "Create a project ✨";
  byId("project-name").value = project?.name || "";
  byId("project-start").value = project?.startDate || localDateKey(new Date());
  byId("project-deadline").value = project?.deadline || "";
  byId("project-priority").value = project?.priority || "Medium";
  openDialog("project-dialog");
  byId("project-name").focus();
}

function openTaskDialog(projectId, task = null, dueDate = null) {
  activeProjectId = projectId;
  editingTaskId = task?.id ?? null;
  const form = byId("task-form");
  form.reset();
  const project = projects.find(item => item.id === projectId);
  byId("task-dialog-title").textContent = task
    ? "Edit task ✨"
    : `Add a task to ${project?.name || "project"}`;
  byId("task-title").value = task?.title || "";
  byId("task-deadline").value = task?.dueDate || dueDate || "";
  byId("task-priority").value = task?.priority || "Medium";
  byId("task-status").value = task?.status || "To Do";
  openDialog("task-dialog");
  byId("task-title").focus();
}

function openApplicationDialog(application = null) {
  editingApplicationId = application?.id ?? null;
  const form = byId("application-form");
  form.reset();
  byId("application-dialog-title").textContent = application ? "Edit application ✨" : "Add an application ✨";
  byId("company-input").value = application?.company || "";
  byId("role-input").value = application?.role || "";
  byId("date-input").value = application?.date || localDateKey(new Date());
  byId("status-input").value = application?.status || "Applied";
  byId("interview-date-input").value = application?.interviewDate || "";
  openDialog("application-dialog");
  byId("company-input").focus();
}

function openMilestoneDialog() {
  const select = byId("milestone-project");
  select.replaceChildren();
  projects.forEach(project => {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = project.name;
    select.append(option);
  });
  if (!projects.length) return showToast("Create a project before adding a milestone.", true);
  byId("milestone-date").value = localDateKey(new Date());
  openDialog("milestone-dialog");
}

function formValues(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function bindForm(formId, submit) {
  byId(formId).addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector('[type="submit"]');
    submitButton.disabled = true;
    try {
      await submit(formValues(form));
      form.closest("dialog").close();
      form.reset();
      await refreshDashboard();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      submitButton.disabled = false;
    }
  });
}

function bindEvents() {
  const profileTrigger = byId("profile-trigger");
  const profileMenu = byId("profile-menu");
  const profileDropdown = byId("profile-dropdown");
  const setProfileMenuOpen = open => {
    profileMenu.hidden = !open;
    profileTrigger.setAttribute("aria-expanded", String(open));
    profileTrigger.setAttribute(
      "aria-label",
      open ? "Close profile menu" : "Open profile menu"
    );
  };
  profileTrigger.addEventListener("click", () => {
    setProfileMenuOpen(profileMenu.hidden);
  });
  document.addEventListener("pointerdown", event => {
    if (!profileDropdown.contains(event.target)) setProfileMenuOpen(false);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !profileMenu.hidden) {
      setProfileMenuOpen(false);
      profileTrigger.focus();
    }
  });
  byId("profile-workspace-link").addEventListener("click", () => {
    setProfileMenuOpen(false);
  });

  byId("add-project").addEventListener("click", () => openProjectDialog());
  byId("hero-add-project").addEventListener("click", () => openProjectDialog());
  byId("add-application").addEventListener("click", () => openApplicationDialog());
  byId("add-milestone").addEventListener("click", openMilestoneDialog);
  byId("kanban-add-task").addEventListener("click", () => {
    if (!projects.length) return showToast("Create a project before adding a task.", true);
    openTaskDialog(projects[0].id);
  });
  bindForm("project-form", values => {
    const method = editingProjectId ? "PUT" : "POST";
    const path = editingProjectId ? `/projects/${editingProjectId}` : "/projects";
    return api(path, { method, body: JSON.stringify(values) });
  });
  bindForm("task-form", values => {
    const method = editingTaskId ? "PATCH" : "POST";
    const path = editingTaskId ? `/tasks/${editingTaskId}` : `/projects/${activeProjectId}/tasks`;
    return api(path, { method, body: JSON.stringify(values) });
  });
  bindForm("application-form", values => {
    const method = editingApplicationId ? "PATCH" : "POST";
    const path = editingApplicationId ? `/applications/${editingApplicationId}` : "/applications";
    return api(path, { method, body: JSON.stringify(values) });
  });
  bindForm("milestone-form", values =>
    api("/milestones", { method: "POST", body: JSON.stringify(values) }));
  document.querySelectorAll("[data-close-dialog]").forEach(button => {
    button.addEventListener("click", () => byId(button.dataset.closeDialog).close());
  });

  document.querySelectorAll("[data-view]").forEach(button => {
    button.addEventListener("click", () => {
      currentView = button.dataset.view;
      updatePlannerLabel();
      renderProjects();
    });
  });
  byId("today-button").addEventListener("click", () => {
    periodDate = new Date();
    updatePlannerLabel();
    renderProjects();
  });
  function shiftPlannerPeriod(direction) {
    if (currentView === "day") periodDate.setDate(periodDate.getDate() + direction);
    else if (currentView === "week") periodDate.setDate(periodDate.getDate() + direction * 7);
    else periodDate = new Date(periodDate.getFullYear(), periodDate.getMonth() + direction, 1);
    updatePlannerLabel();
    renderProjects();
  }
  byId("previous-period").addEventListener("click", () => shiftPlannerPeriod(-1));
  byId("next-period").addEventListener("click", () => shiftPlannerPeriod(1));

  document.querySelectorAll("[data-calendar-view]").forEach(button => {
    button.addEventListener("click", () => {
      calendarMode = button.dataset.calendarView;
      renderCalendar();
    });
  });
  byId("calendar-previous").addEventListener("click", () => {
    if (calendarMode === "month") calendarDate = new Date(calendarDate.getFullYear(), calendarDate.getMonth() - 1, 1);
    else calendarDate.setDate(calendarDate.getDate() - 7);
    renderCalendar();
  });
  byId("calendar-next").addEventListener("click", () => {
    if (calendarMode === "month") calendarDate = new Date(calendarDate.getFullYear(), calendarDate.getMonth() + 1, 1);
    else calendarDate.setDate(calendarDate.getDate() + 7);
    renderCalendar();
  });
  byId("calendar-today").addEventListener("click", () => {
    calendarDate = new Date();
    renderCalendar();
  });
  byId("notification-toggle").addEventListener("click", () => {
    byId("notification-panel").hidden = !byId("notification-panel").hidden;
    loadNotifications();
  });
  byId("refresh-notifications").addEventListener("click", loadNotifications);

  const themeToggle = () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    const label = dark ? "Switch to light mode" : "Switch to dark mode";
    byId("theme-toggle").setAttribute("aria-label", label);
    byId("theme-icon").textContent = dark ? "☀" : "☾";
    byId("top-theme-toggle").setAttribute("aria-label", label);
    byId("top-theme-toggle").textContent = dark ? "☀" : "☾";
    if (window.currentAnalytics) renderCharts(window.currentAnalytics);
  };
  byId("theme-toggle").addEventListener("click", themeToggle);
  byId("top-theme-toggle").addEventListener("click", themeToggle);
  byId("mobile-menu").addEventListener("click", () => byId("sidebar").classList.toggle("open"));
  document.querySelectorAll(".side-nav .nav-link").forEach(link => {
    link.addEventListener("click", () => {
      document.querySelectorAll(".side-nav .nav-link").forEach(item => item.classList.remove("active"));
      link.classList.add("active");
      byId("sidebar").classList.remove("open");
    });
  });
  byId("career-role").addEventListener("change", () => renderInterviewQuestions(byId("career-role").value));
  byId("career-form").addEventListener("submit", async event => {
    event.preventDefault();
    const formData = formValues(event.currentTarget);
    const { jobDescription, ...profile } = formData;
    try {
      await api("/career/profile", { method: "PUT", body: JSON.stringify(profile) });
      const result = await api("/career/analyze", {
        method: "POST",
        body: JSON.stringify({ ...profile, jobDescription })
      });
      renderCareerAnalysis(result);
      showToast("Your local career roadmap is ready.");
    } catch (error) {
      showToast(error.message, true);
    }
  });
  byId("resume-form").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const result = await api("/career/resume-review", {
        method: "POST",
        body: JSON.stringify({ text: byId("resume-text").value })
      });
      const root = byId("resume-feedback");
      root.replaceChildren();
      const summary = document.createElement("p");
      summary.textContent = `${result.wordCount} words · local structural review`;
      const list = document.createElement("ul");
      list.className = "career-list";
      result.suggestions.forEach(suggestion => {
        const item = document.createElement("li");
        item.textContent = suggestion;
        list.append(item);
      });
      const note = document.createElement("p");
      note.className = "chart-subtitle";
      note.textContent = result.disclaimer;
      root.append(summary, list, note);
    } catch (error) {
      showToast(error.message, true);
    }
  });
}

async function initialize() {
  byId("today-label").textContent = new Date().toLocaleDateString(undefined, {
    weekday: "short", month: "short", day: "numeric"
  });
  updatePlannerLabel();
  bindEvents();
  try {
    await refreshDashboard();
    await loadCareerProfile();
  } catch (error) {
    showToast(`Could not load Trackly: ${error.message}`, true);
    byId("planner-projects").textContent =
      "Trackly could not load your workspace. Check the server and refresh.";
  }
}

initialize();
