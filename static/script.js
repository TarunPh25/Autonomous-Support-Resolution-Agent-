// API Base URLs
const API_SETTINGS = '/api/settings';
const API_TICKETS = '/api/tickets';
const API_PROCESS = '/api/process';
const API_PROCESS_ALL = '/api/process_all';
const API_DATABASE = '/api/database';
const API_RESET = '/api/reset';

// Application State
let state = {
  tickets: [],
  selectedTicketId: null,
  activeFilter: 'all',
  activeView: 'workspace', // 'workspace' or 'database'
  dbData: {
    customer_profiles: [],
    knowledge_base: [],
    orders: [],
    products: []
  },
  currentDbTab: 'kb',
  isProcessingAll: false
};

// Initial Setup on Page Load
document.addEventListener('DOMContentLoaded', () => {
  init();
  setupEventListeners();
});

async function init() {
  showToast('Initializing Support Dashboard...', 'info');
  await fetchSettings();
  await fetchTickets();
  await fetchDatabase();
  lucide.createIcons();
}

function setupEventListeners() {
  // Lucide icons setup
  lucide.createIcons();
}

// ── FETCH API FUNCTIONS ──

async function fetchSettings() {
  try {
    const res = await fetch(API_SETTINGS);
    const data = await res.json();
    updateModeBadge(data.mode, data.llm_available);
  } catch (err) {
    console.error('Error fetching settings:', err);
    showToast('Failed to connect to backend configuration.', 'error');
  }
}

async function fetchTickets() {
  try {
    const res = await fetch(API_TICKETS);
    state.tickets = await res.json();
    renderTicketsList();
    updateDashboardStats();
    
    // Refresh currently selected ticket if it exists
    if (state.selectedTicketId) {
      const updated = state.tickets.find(t => t.ticket_id === state.selectedTicketId);
      if (updated) {
        renderTicketInspector(updated);
      }
    }
  } catch (err) {
    console.error('Error fetching tickets:', err);
    showToast('Failed to load tickets queue.', 'error');
  }
}

async function fetchDatabase() {
  try {
    const res = await fetch(API_DATABASE);
    state.dbData = await res.json();
    renderDatabaseInspectors();
  } catch (err) {
    console.error('Error fetching database:', err);
  }
}

// ── UI RENDER FUNCTIONS ──

function updateModeBadge(mode, llmAvailable) {
  const badge = document.getElementById('mode-badge');
  const text = document.getElementById('current-mode-text');
  
  if (mode === 'llm') {
    text.textContent = 'Groq LLM Mode';
    badge.style.background = 'rgba(139, 92, 246, 0.15)';
    badge.style.borderColor = 'rgba(139, 92, 246, 0.4)';
  } else {
    text.textContent = 'Deterministic Mode';
    badge.style.background = 'rgba(99, 102, 241, 0.15)';
    badge.style.borderColor = 'var(--border-glow)';
  }
}

function updateDashboardStats() {
  const total = state.tickets.length;
  const resolved = state.tickets.filter(t => t.status === 'resolved').length;
  const escalated = state.tickets.filter(t => t.status === 'escalated').length;
  const needsInfo = state.tickets.filter(t => t.status === 'needs_info').length;
  
  const successRate = total > 0 ? Math.round(((resolved) / total) * 100) : 0;
  
  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-resolved').textContent = resolved;
  document.getElementById('stat-escalated').textContent = escalated;
  document.getElementById('stat-success-rate').textContent = `${successRate}%`;
}

function renderTicketsList() {
  const list = document.getElementById('tickets-list');
  const searchInput = document.getElementById('search-input').value.toLowerCase();
  
  // Filter tickets
  let filtered = state.tickets;
  
  // Filter by status tab
  if (state.activeFilter !== 'all') {
    filtered = filtered.filter(t => t.status === state.activeFilter);
  }
  
  // Filter by search text
  if (searchInput) {
    filtered = filtered.filter(t => 
      t.ticket_id.toLowerCase().includes(searchInput) ||
      t.subject.toLowerCase().includes(searchInput) ||
      t.customer_email.toLowerCase().includes(searchInput) ||
      t.body.toLowerCase().includes(searchInput)
    );
  }
  
  document.getElementById('ticket-count').textContent = filtered.length;
  
  if (filtered.length === 0) {
    list.innerHTML = `<div class="list-placeholder">No tickets found matching "${state.activeFilter}"</div>`;
    return;
  }
  
  list.innerHTML = '';
  filtered.forEach(ticket => {
    const item = document.createElement('div');
    item.className = `ticket-item ${ticket.ticket_id === state.selectedTicketId ? 'active' : ''}`;
    item.onclick = () => selectTicket(ticket.ticket_id);
    
    let tierText = 'Standard';
    let tierClass = '';
    if (ticket.tier === 3) { tierText = 'VIP'; tierClass = 'tier-vip'; }
    else if (ticket.tier === 2) { tierText = 'Premium'; tierClass = 'tier-premium'; }
    
    let categoryBadge = ticket.category !== 'unclassified' ? `<span class="ticket-item-category">${ticket.category}</span>` : '';
    
    item.innerHTML = `
      <div class="ticket-item-header">
        <span class="ticket-item-id">${ticket.ticket_id}</span>
        <span class="ticket-item-status-badge status-${ticket.status.replace('_', '-')}">${ticket.status.replace('_', ' ')}</span>
      </div>
      <div class="ticket-item-subject">${ticket.subject}</div>
      <div class="ticket-item-email">${ticket.customer_email}</div>
      <div class="ticket-item-footer">
        <span class="ticket-item-tier ${tierClass}">${tierText}</span>
        ${categoryBadge}
      </div>
    `;
    list.appendChild(item);
  });
}

function selectTicket(ticketId) {
  state.selectedTicketId = ticketId;
  
  // Update active states in list
  const items = document.querySelectorAll('.ticket-item');
  items.forEach(el => el.classList.remove('active'));
  
  // Reload details
  const ticket = state.tickets.find(t => t.ticket_id === ticketId);
  if (ticket) {
    renderTicketInspector(ticket);
  }
  
  // Switch to workspace view if in db view
  if (state.activeView !== 'workspace') {
    toggleView();
  }
  
  // Trigger active state rendering
  renderTicketsList();
}

function renderTicketInspector(ticket) {
  // Hide empty state, show inspector
  document.getElementById('empty-state').classList.add('hidden');
  const inspector = document.getElementById('ticket-inspector');
  inspector.classList.remove('hidden');
  
  // Map raw details
  document.getElementById('ins-ticket-id').textContent = ticket.ticket_id;
  document.getElementById('ins-subject').textContent = ticket.subject;
  document.getElementById('ins-customer-email').textContent = ticket.customer_email;
  document.getElementById('ins-body').textContent = ticket.body;
  document.getElementById('ins-expected-action').textContent = ticket.expected_action || "Standard resolution policy applies.";
  
  // Tiers and source
  const tierBadge = document.getElementById('ins-tier-badge');
  tierBadge.className = 'badge';
  if (ticket.tier === 3) {
    tierBadge.textContent = 'VIP (Tier 3)';
    tierBadge.classList.add('tier-vip');
  } else if (ticket.tier === 2) {
    tierBadge.textContent = 'Premium (Tier 2)';
    tierBadge.classList.add('tier-premium');
  } else {
    tierBadge.textContent = 'Standard (Tier 1)';
  }
  
  document.getElementById('ins-source-badge').textContent = ticket.source;

  // Render reasoning timeline
  const feed = document.getElementById('console-steps-feed');
  const resPanel = document.getElementById('resolution-panel');
  const metaBadges = document.getElementById('console-meta-badges');
  
  // Reset console badges
  metaBadges.innerHTML = '';
  
  if (ticket.audit_log && ticket.audit_log.steps && ticket.audit_log.steps.length > 0) {
    feed.innerHTML = '';
    
    // Add meta badges
    metaBadges.innerHTML = `
      <span class="badge">Steps: ${ticket.steps_count}</span>
      <span class="badge">Speed: ${Math.round(ticket.processing_time_ms)}ms</span>
    `;
    
    ticket.audit_log.steps.forEach(step => {
      const stepEl = document.createElement('div');
      stepEl.className = 'react-step';
      
      let observationHTML = '';
      if (step.observation) {
        // Format observation JSON prettily
        const obsStr = JSON.stringify(step.observation, null, 2);
        const uniqueId = `obs-collapse-${step.step}-${ticket.ticket_id}`;
        
        observationHTML = `
          <div class="step-observation">
            <div class="obs-header" onclick="toggleObservationCollapse('${uniqueId}')">
              <span>Observation Output (${step.action})</span>
              <i data-lucide="chevron-down" style="width: 14px;"></i>
            </div>
            <pre id="${uniqueId}" style="margin-top: 8px;">${obsStr}</pre>
          </div>
        `;
      }
      
      let actionHTML = '';
      if (step.action) {
        actionHTML = `
          <div class="step-action-card">
            <i data-lucide="play" style="width: 12px; color: var(--secondary);"></i>
            <span class="action-label">ACT:</span>
            <span class="action-name">${step.action}</span>
            <span class="action-input">(${JSON.stringify(step.action_input)})</span>
          </div>
        `;
      }
      
      stepEl.innerHTML = `
        <div class="step-num-bubble">${step.step}</div>
        <div class="step-header">
          <div class="step-title">Reasoning Step ${step.step}</div>
          <div class="step-meta">
            <span class="step-latency">${Math.round(step.latency_ms || 0)}ms</span>
          </div>
        </div>
        <div class="step-thought">
          ${step.thought || 'Planning next actions...'}
        </div>
        ${actionHTML}
        ${observationHTML}
      `;
      feed.appendChild(stepEl);
    });
    
    // Render resolution
    resPanel.classList.remove('hidden');
    
    const statusText = document.getElementById('resolution-status-text');
    const statusIcon = document.getElementById('resolution-status-icon');
    const confPill = document.getElementById('resolution-confidence-pill');
    
    statusText.textContent = ticket.status.toUpperCase();
    confPill.textContent = ticket.confidence ? `${Math.round(ticket.confidence * 100)}%` : '100%';
    
    if (ticket.status === 'resolved') {
      resPanel.className = 'resolution-panel text-emerald';
      statusIcon.setAttribute('data-lucide', 'check-circle');
    } else if (ticket.status === 'escalated') {
      resPanel.className = 'resolution-panel escalated text-amber';
      statusIcon.setAttribute('data-lucide', 'arrow-up-right');
    } else if (ticket.status === 'needs_info') {
      resPanel.className = 'resolution-panel text-blue';
      statusIcon.setAttribute('data-lucide', 'help-circle');
    }
    
    document.getElementById('resolution-message-box').textContent = ticket.final_resolution || "Escalated for human resolution.";
    
    // Policy tags
    const tagsContainer = document.getElementById('policies-tags');
    tagsContainer.innerHTML = '';
    const policies = ticket.audit_log.policy_references || [];
    
    if (policies.length > 0) {
      policies.forEach(p => {
        const tag = document.createElement('span');
        tag.className = 'policy-tag';
        tag.textContent = p;
        tagsContainer.appendChild(tag);
      });
    } else {
      tagsContainer.innerHTML = '<span style="font-size: 0.75rem; color: var(--text-muted);">None cited</span>';
    }
    
  } else {
    // Show placeholder if not run
    feed.innerHTML = `
      <div class="console-placeholder">
        <i data-lucide="terminal" class="big-icon"></i>
        <p>This ticket is pending resolution. Click "Resolve Ticket" below to trigger the reasoning loop.</p>
      </div>
    `;
    resPanel.classList.add('hidden');
  }
  
  // Recreate Lucide Icons
  lucide.createIcons();
}

function toggleObservationCollapse(id) {
  const el = document.getElementById(id);
  if (el) {
    el.classList.toggle('hidden');
  }
}

// ── RUN ACTION FUNCTIONS ──

async function runCurrentTicket() {
  if (!state.selectedTicketId) return;
  
  const loader = document.getElementById('ticket-run-loader');
  const runBtn = document.getElementById('btn-run-ticket');
  
  loader.classList.remove('hidden');
  runBtn.classList.add('hidden');
  
  try {
    const res = await fetch(`${API_PROCESS}/${state.selectedTicketId}`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Processing error');
    }
    
    showToast(`Ticket ${state.selectedTicketId} resolved successfully.`, 'success');
    await fetchTickets(); // Refresh lists and counts
  } catch (err) {
    console.error(err);
    showToast(`Error: ${err.message}`, 'error');
  } finally {
    loader.classList.add('hidden');
    runBtn.classList.remove('hidden');
  }
}

async function processAllTickets() {
  if (state.isProcessingAll) return;
  
  state.isProcessingAll = true;
  const btn = document.getElementById('btn-process-all');
  btn.disabled = true;
  btn.innerHTML = `<div class="spinner" style="width: 14px; height: 14px;"></div><span>Processing...</span>`;
  
  showToast('Initiating concurrent processing of all support tickets...', 'info');
  
  try {
    const res = await fetch(API_PROCESS_ALL, { method: 'POST' });
    const data = await res.json();
    
    if (data.status === 'processing') {
      // Poll ticket states every 1.5 seconds to update UI progress
      let dots = 0;
      const pollInterval = setInterval(async () => {
        await fetchTickets();
        
        const pendingCount = state.tickets.filter(t => t.status === 'pending').length;
        if (pendingCount === 0) {
          clearInterval(pollInterval);
          state.isProcessingAll = false;
          btn.disabled = false;
          btn.innerHTML = `<i data-lucide="play-circle"></i><span>Process All</span>`;
          showToast('All support tickets successfully processed!', 'success');
          lucide.createIcons();
        }
      }, 1500);
    }
  } catch (err) {
    console.error(err);
    showToast('Failed to start bulk processor.', 'error');
    state.isProcessingAll = false;
    btn.disabled = false;
    btn.innerHTML = `<i data-lucide="play-circle"></i><span>Process All</span>`;
    lucide.createIcons();
  }
}

async function resetDatabase() {
  if (!confirm('Are you sure you want to delete all audit logs and reset the transaction simulation cache? This is useful to run from scratch.')) return;
  
  showToast('Resetting simulation...', 'info');
  try {
    const res = await fetch(API_RESET, { method: 'POST' });
    if (res.ok) {
      showToast('Simulation successfully reset to clean slate.', 'success');
      state.selectedTicketId = null;
      document.getElementById('ticket-inspector').classList.add('hidden');
      document.getElementById('empty-state').classList.remove('hidden');
      await fetchTickets();
      await fetchDatabase();
    }
  } catch (err) {
    showToast('Error resetting simulation.', 'error');
  }
}

// ── SIDEBAR SEARCH & FILTERS ──

function setFilter(element) {
  document.querySelectorAll('.filter-tab').forEach(el => el.classList.remove('active'));
  element.classList.add('active');
  state.activeFilter = element.getAttribute('data-status');
  renderTicketsList();
}

function filterTickets() {
  renderTicketsList();
}

// ── MODALS VIEW CONTROLLERS ──

function openCreateTicketModal() {
  document.getElementById('modal-create-ticket').classList.remove('hidden');
}
function closeCreateTicketModal() {
  document.getElementById('modal-create-ticket').classList.add('hidden');
  document.getElementById('create-ticket-form').reset();
}

async function handleCreateTicketSubmit(event) {
  event.preventDefault();
  
  const email = document.getElementById('ticket-email').value;
  const tier = parseInt(document.getElementById('ticket-tier').value);
  const source = document.getElementById('ticket-source').value;
  const subject = document.getElementById('ticket-subject').value;
  const body = document.getElementById('ticket-body').value;
  const expected = document.getElementById('ticket-expected').value;
  
  try {
    const res = await fetch(API_TICKETS, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        customer_email: email,
        tier: tier,
        source: source,
        subject: subject,
        body: body,
        expected_action: expected
      })
    });
    
    if (res.ok) {
      const newTicket = await res.json();
      showToast(`Ticket ${newTicket.ticket_id} created successfully!`, 'success');
      closeCreateTicketModal();
      await fetchTickets();
      selectTicket(newTicket.ticket_id);
    } else {
      showToast('Failed to create ticket.', 'error');
    }
  } catch (err) {
    showToast('Error creating ticket.', 'error');
  }
}

function openSettingsModal() {
  document.getElementById('modal-settings').classList.remove('hidden');
}
function closeSettingsModal() {
  document.getElementById('modal-settings').classList.add('hidden');
}

async function handleSettingsSubmit(event) {
  event.preventDefault();
  
  const mode = document.querySelector('input[name="agent-mode-radio"]:checked').value;
  const key = document.getElementById('groq-api-key').value;
  
  try {
    const res = await fetch(API_SETTINGS, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode: mode,
        groq_api_key: key || null
      })
    });
    
    const data = await res.json();
    if (res.ok) {
      showToast(`Reasoning engine updated to ${mode.toUpperCase()} mode.`, 'success');
      closeSettingsModal();
      updateModeBadge(data.mode, data.llm_available);
    } else {
      showToast(data.detail || 'Failed to update settings.', 'error');
    }
  } catch (err) {
    showToast('Error saving settings.', 'error');
  }
}

// Toggle between Main Workspace & Database view
function toggleView() {
  const toggleBtn = document.getElementById('view-toggle-btn');
  const workspace = document.getElementById('ticket-workspace');
  const database = document.getElementById('database-workspace');
  
  if (state.activeView === 'workspace') {
    state.activeView = 'database';
    workspace.classList.remove('active');
    database.classList.add('active');
    toggleBtn.innerHTML = `<i data-lucide="layout"></i>`;
    toggleBtn.setAttribute('title', 'Workspace View');
  } else {
    state.activeView = 'workspace';
    database.classList.remove('active');
    workspace.classList.add('active');
    toggleBtn.innerHTML = `<i data-lucide="database"></i>`;
    toggleBtn.setAttribute('title', 'Inspect Database');
  }
  lucide.createIcons();
}

// ── DATABASE INSPECTOR FUNCTIONS ──

function switchDbTab(button) {
  document.querySelectorAll('.db-tab').forEach(el => el.classList.remove('active'));
  button.classList.add('active');
  
  const dbName = button.getAttribute('data-db');
  state.currentDbTab = dbName;
  
  document.querySelectorAll('.db-panel').forEach(panel => panel.classList.remove('active'));
  document.getElementById(`db-panel-${dbName}`).classList.add('active');
}

function renderDatabaseInspectors() {
  // 1. Knowledge Base
  const kbGrid = document.getElementById('kb-grid-content');
  if (state.dbData.knowledge_base.length > 0) {
    kbGrid.innerHTML = '';
    state.dbData.knowledge_base.forEach(art => {
      const card = document.createElement('div');
      card.className = 'policy-card';
      card.innerHTML = `
        <div class="policy-card-header">
          <span class="policy-card-title">${art.title}</span>
          <span class="policy-card-id">${art.policy_id}</span>
        </div>
        <div class="policy-card-category">${art.category}</div>
        <div class="policy-card-body">${art.content}</div>
      `;
      kbGrid.appendChild(card);
    });
  } else {
    kbGrid.innerHTML = '<div style="color: var(--text-muted);">No KB articles loaded</div>';
  }
  
  // 2. Customer Profiles
  const profilesTable = document.getElementById('profiles-table-content');
  if (state.dbData.customer_profiles.length > 0) {
    profilesTable.innerHTML = '';
    state.dbData.customer_profiles.forEach(p => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${p.customer_id}</strong></td>
        <td>${p.name}</td>
        <td>${p.email}</td>
        <td><span class="status-pill" style="background: rgba(99,102,241,0.15); color: var(--primary);">${p.tier}</span></td>
        <td>${p.account_age_months} months</td>
        <td>${p.total_orders}</td>
        <td>$${p.total_spent.toFixed(2)}</td>
        <td style="font-size: 0.75rem; color: var(--text-muted);">${p.notes}</td>
      `;
      profilesTable.appendChild(tr);
    });
  }
  
  // 3. Orders Ledger
  const ordersTable = document.getElementById('orders-table-content');
  if (state.dbData.orders.length > 0) {
    ordersTable.innerHTML = '';
    state.dbData.orders.forEach(o => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${o.order_id}</strong></td>
        <td>${o.customer_id}</td>
        <td>${o.product_id}</td>
        <td>${o.quantity}</td>
        <td>$${o.amount.toFixed(2)}</td>
        <td><span class="status-pill ${o.status}">${o.status}</span></td>
        <td>${o.order_date}</td>
        <td>${o.return_deadline || '-'}</td>
        <td>${o.refund_status ? `<span class="status-pill delivered">${o.refund_status}</span>` : '-'}</td>
        <td style="font-size: 0.75rem; color: var(--text-muted);">${o.notes || ''}</td>
      `;
      ordersTable.appendChild(tr);
    });
  }
  
  // 4. Products Catalog
  const productsTable = document.getElementById('products-table-content');
  if (state.dbData.products.length > 0) {
    productsTable.innerHTML = '';
    state.dbData.products.forEach(p => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${p.product_id}</strong></td>
        <td>${p.name}</td>
        <td>${p.category}</td>
        <td>$${p.price.toFixed(2)}</td>
        <td><span class="status-pill ${p.stock > 0 ? 'delivered' : 'processing'}">${p.stock > 0 ? 'In Stock' : 'Out of Stock'}</span></td>
        <td>${p.return_window_days} days</td>
        <td>${p.warranty_months} months</td>
      `;
      productsTable.appendChild(tr);
    });
  }
}

// ── UTILITY CLIENT FUNCTIONS ──

function copyResolutionMessage() {
  const box = document.getElementById('resolution-message-box');
  navigator.clipboard.writeText(box.textContent);
  showToast('Resolution text copied to clipboard.', 'success');
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  
  let icon = 'info';
  if (type === 'success') icon = 'check';
  else if (type === 'error') icon = 'alert-triangle';
  
  toast.innerHTML = `
    <i data-lucide="${icon}"></i>
    <span>${message}</span>
  `;
  container.appendChild(toast);
  lucide.createIcons();
  
  setTimeout(() => {
    toast.style.animation = 'slide-in 0.3s reverse';
    setTimeout(() => {
      toast.remove();
    }, 300);
  }, 3500);
}
