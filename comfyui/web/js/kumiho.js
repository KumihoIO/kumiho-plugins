/**
 * Kumiho ComfyUI Frontend Extension
 * 
 * Provides UI enhancements and client-side functionality for Kumiho nodes.
 * 
 * Features:
 * - Sidebar panel for asset browser
 * - Settings management
 * - Lineage visualization
 * - Real-time registration feedback
 */

import { app } from "../../scripts/app.js";

// =============================================================================
// Constants
// =============================================================================

const KUMIHO_VERSION = "0.1.0";
const KUMIHO_COLORS = {
    primary: "#ff6b35",      // Kumiho orange
    secondary: "#1e3a5f",    // Dark blue
    background: "#0d1b2a",   // Very dark blue
    success: "#4caf50",
    error: "#f44336",
    warning: "#ff9800",
};

// =============================================================================
// Kumiho Settings Manager
// =============================================================================

class KumihoSettings {
    constructor() {
        this.settings = this.loadSettings();
    }
    
    loadSettings() {
        const defaults = {
            apiEndpoint: "https://api.kumiho.io",
            autoRegister: true,
            createLineage: true,
            showNotifications: true,
        };

        const stored = localStorage.getItem("kumiho_settings");
        let parsed = {};

        if (stored) {
            try {
                parsed = JSON.parse(stored) || {};
            } catch (e) {
                console.warn("Failed to parse Kumiho settings:", e);
            }
        }

        const merged = { ...defaults, ...parsed };

        return merged;
    }
    
    saveSettings() {
        localStorage.setItem("kumiho_settings", JSON.stringify(this.settings));
    }
    
    get(key) {
        return this.settings[key];
    }
    
    set(key, value) {
        this.settings[key] = value;
        this.saveSettings();
    }
}

const kumihoSettings = new KumihoSettings();

// =============================================================================
// Kumiho Catalog API Client
// =============================================================================

class KumihoCatalogAPI {
    /**
     * Fetch available projects from the catalog
     */
    async getProjects() {
        try {
            const response = await fetch("/kumiho/catalog/projects");
            const data = await response.json();
            return data.projects || [];
        } catch (e) {
            console.warn("[Kumiho] Failed to fetch projects:", e);
            return [];
        }
    }
    
    /**
     * Fetch spaces for a project (or all spaces)
     */
    async getSpaces(project = null) {
        try {
            const url = project 
                ? `/kumiho/catalog/spaces?project=${encodeURIComponent(project)}`
                : "/kumiho/catalog/spaces";
            const response = await fetch(url);
            const data = await response.json();
            return data.spaces || [];
        } catch (e) {
            console.warn("[Kumiho] Failed to fetch spaces:", e);
            return [];
        }
    }
    
    /**
     * Fetch items in a space
     */
    async getItems(spacePath) {
        try {
            const response = await fetch(`/kumiho/catalog/items?space=${encodeURIComponent(spacePath)}`);
            const data = await response.json();
            return data.items || [];
        } catch (e) {
            console.warn("[Kumiho] Failed to fetch items:", e);
            return [];
        }
    }
    
    /**
     * Search for items across all projects
     */
    async search(query, kind = "") {
        try {
            const params = new URLSearchParams();
            params.set("q", query);
            if (kind) params.set("kind", kind);
            
            const response = await fetch(`/kumiho/catalog/search?${params}`);
            const data = await response.json();
            return data.results || [];
        } catch (e) {
            console.warn("[Kumiho] Search failed:", e);
            return [];
        }
    }
    
    /**
     * Trigger a catalog refresh
     */
    async refresh() {
        try {
            const response = await fetch("/kumiho/catalog/refresh", { method: "POST" });
            const data = await response.json();
            return data.status === "ok";
        } catch (e) {
            console.warn("[Kumiho] Refresh failed:", e);
            return false;
        }
    }
}

const kumihoCatalogAPI = new KumihoCatalogAPI();

// =============================================================================
// Kumiho Sidebar Panel
// =============================================================================

class KumihoSidebarPanel {
    constructor() {
        this.isOpen = false;
        this.container = null;
        this.currentTab = "assets";
        this.currentProject = null;
        this.currentSpace = null;
    }
    
    create() {
        // Create sidebar container
        this.container = document.createElement("div");
        this.container.id = "kumiho-sidebar";
        this.container.className = "kumiho-sidebar";
        this.container.innerHTML = this.getHTML();
        
        // Add styles
        this.addStyles();
        
        // Add to DOM
        document.body.appendChild(this.container);
        
        // Setup event listeners
        this.setupEventListeners();
    }
    
    getHTML() {
        return `
            <div class="kumiho-sidebar-header">
                <div class="kumiho-logo">
                    <span class="kumiho-emoji">🦊</span>
                    <span class="kumiho-title">Kumiho Cloud</span>
                </div>
                <button class="kumiho-close-btn" id="kumiho-close">×</button>
            </div>
            
            <div class="kumiho-tabs">
                <button class="kumiho-tab active" data-tab="assets">Assets</button>
                <button class="kumiho-tab" data-tab="lineage">Lineage</button>
                <button class="kumiho-tab" data-tab="settings">Settings</button>
            </div>
            
            <div class="kumiho-tab-content" id="kumiho-tab-content">
                ${this.getAssetsTabHTML()}
            </div>
            
            <div class="kumiho-footer">
                <span>Kumiho v${KUMIHO_VERSION}</span>
                <a href="https://docs.kumiho.io" target="_blank">Docs</a>
            </div>
        `;
    }
    
    getAssetsTabHTML() {
        return `
            <div class="kumiho-search">
                <input type="text" id="kumiho-search-input" placeholder="Search assets...">
                <button id="kumiho-search-btn">🔍</button>
            </div>
            
            <div class="kumiho-filters">
                <select id="kumiho-project-filter">
                    <option value="">Select Project...</option>
                </select>
                <select id="kumiho-space-filter">
                    <option value="">Select Space...</option>
                </select>
                <select id="kumiho-type-filter">
                    <option value="">All Types</option>
                    <option value="checkpoint">Checkpoints</option>
                    <option value="lora">LoRAs</option>
                    <option value="controlnet">ControlNet</option>
                    <option value="vae">VAE</option>
                    <option value="image">Images</option>
                </select>
            </div>
            
            <div class="kumiho-asset-list" id="kumiho-asset-list">
                <div class="kumiho-placeholder">
                    <p>Select a project and space to browse assets,<br>or search by name</p>
                    <button id="kumiho-refresh-catalog" class="kumiho-btn-secondary">
                        🔄 Refresh Catalog
                    </button>
                </div>
            </div>
            
            <div class="kumiho-recent">
                <h4>Recent Registrations</h4>
                <div id="kumiho-recent-list">
                    <p class="kumiho-empty">No recent registrations</p>
                </div>
            </div>
        `;
    }
    
    async initAssetsTab() {
        // Load projects
        const projects = await kumihoCatalogAPI.getProjects();
        const projectSelect = document.getElementById("kumiho-project-filter");
        if (projectSelect) {
            projectSelect.innerHTML = '<option value="">Select Project...</option>';
            projects.forEach(p => {
                const opt = document.createElement("option");
                opt.value = p;
                opt.textContent = p;
                projectSelect.appendChild(opt);
            });
        }
        
        // Setup event listeners for asset browsing
        this.setupAssetsBrowseListeners();
    }
    
    setupAssetsBrowseListeners() {
        // Project selection
        document.getElementById("kumiho-project-filter")?.addEventListener("change", async (e) => {
            this.currentProject = e.target.value;
            const spaces = await kumihoCatalogAPI.getSpaces(this.currentProject);
            const spaceSelect = document.getElementById("kumiho-space-filter");
            if (spaceSelect) {
                spaceSelect.innerHTML = '<option value="">Select Space...</option>';
                spaces.forEach(s => {
                    const opt = document.createElement("option");
                    opt.value = s;
                    opt.textContent = s;
                    spaceSelect.appendChild(opt);
                });
            }
        });
        
        // Space selection
        document.getElementById("kumiho-space-filter")?.addEventListener("change", async (e) => {
            this.currentSpace = e.target.value;
            await this.loadItemsForSpace(this.currentSpace);
        });
        
        // Search
        document.getElementById("kumiho-search-btn")?.addEventListener("click", () => this.searchAssets());
        document.getElementById("kumiho-search-input")?.addEventListener("keypress", (e) => {
            if (e.key === "Enter") this.searchAssets();
        });
        
        // Type filter
        document.getElementById("kumiho-type-filter")?.addEventListener("change", () => this.searchAssets());
        
        // Refresh catalog
        document.getElementById("kumiho-refresh-catalog")?.addEventListener("click", async () => {
            this.showToast("Refreshing catalog...", "info");
            const success = await kumihoCatalogAPI.refresh();
            if (success) {
                this.showToast("Catalog refresh triggered", "success");
                // Reload projects
                await this.initAssetsTab();
            } else {
                this.showToast("Refresh failed", "error");
            }
        });
    }
    
    async loadItemsForSpace(spacePath) {
        const listEl = document.getElementById("kumiho-asset-list");
        if (!listEl || !spacePath) return;
        
        listEl.innerHTML = '<div class="kumiho-loading">Loading...</div>';
        
        const items = await kumihoCatalogAPI.getItems(spacePath);
        
        if (items.length === 0) {
            listEl.innerHTML = '<div class="kumiho-placeholder"><p>No items found in this space</p></div>';
            return;
        }
        
        listEl.innerHTML = items.map(item => `
            <div class="kumiho-asset-item" data-kref="${item.kref}">
                <div class="kumiho-asset-name">${item.name}</div>
                <div class="kumiho-asset-kref">${item.kref}</div>
                <button class="kumiho-copy-btn" title="Copy kref">📋</button>
                <button class="kumiho-use-btn" title="Create Load Asset node">➕</button>
            </div>
        `).join("");
        
        // Add click handlers for copy and use buttons
        listEl.querySelectorAll(".kumiho-copy-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                const kref = e.target.closest(".kumiho-asset-item").dataset.kref;
                navigator.clipboard.writeText(kref);
                this.showToast("kref copied to clipboard", "success");
            });
        });
        
        listEl.querySelectorAll(".kumiho-use-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                const kref = e.target.closest(".kumiho-asset-item").dataset.kref;
                this.createLoadAssetNode(kref);
            });
        });
    }
    
    async searchAssets() {
        const query = document.getElementById("kumiho-search-input")?.value || "";
        const kind = document.getElementById("kumiho-type-filter")?.value || "";
        const listEl = document.getElementById("kumiho-asset-list");
        
        if (!query && !kind) {
            this.showToast("Enter a search term or select a type", "warning");
            return;
        }
        
        listEl.innerHTML = '<div class="kumiho-loading">Searching...</div>';
        
        const results = await kumihoCatalogAPI.search(query, kind);
        
        if (results.length === 0) {
            listEl.innerHTML = '<div class="kumiho-placeholder"><p>No results found</p></div>';
            return;
        }
        
        listEl.innerHTML = results.map(item => `
            <div class="kumiho-asset-item" data-kref="${item.kref}">
                <div class="kumiho-asset-name">${item.name}</div>
                <div class="kumiho-asset-kind">${item.kind}</div>
                <div class="kumiho-asset-path">${item.path}</div>
                <button class="kumiho-copy-btn" title="Copy kref">📋</button>
                <button class="kumiho-use-btn" title="Create Load Asset node">➕</button>
            </div>
        `).join("");
        
        // Add click handlers
        listEl.querySelectorAll(".kumiho-copy-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                const kref = e.target.closest(".kumiho-asset-item").dataset.kref;
                navigator.clipboard.writeText(kref);
                this.showToast("kref copied to clipboard", "success");
            });
        });
        
        listEl.querySelectorAll(".kumiho-use-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                const kref = e.target.closest(".kumiho-asset-item").dataset.kref;
                this.createLoadAssetNode(kref);
            });
        });
    }
    
    createLoadAssetNode(kref) {
        // Parse kref to determine asset type
        let assetType = "checkpoint";
        let itemName = "";
        let tag = "";
        const kindMatch = kref.match(/\.(\w+)(\?|$)/);
        if (kindMatch) {
            assetType = kindMatch[1];
        }
        const itemMatch = kref.match(/\/([^\/]+)\.\w+(?:\?|$)/);
        if (itemMatch) {
            itemName = itemMatch[1];
        }
        const tagMatch = kref.match(/[?&]r=([^&]+)/);
        if (tagMatch) {
            tag = decodeURIComponent(tagMatch[1]);
        }
        
        // Create node via LiteGraph
        if (typeof app !== "undefined" && app.graph) {
            const node = app.graph.add(LiteGraph.createNode("KumihoLoadAsset"));
            if (node) {
                // Set node properties
                node.widgets?.find(w => w.name === "kref_uri")?.value = kref;
                node.widgets?.find(w => w.name === "item_kind")?.value = assetType;
                if (itemName) {
                    node.widgets?.find(w => w.name === "item_name")?.value = itemName;
                }
                if (tag) {
                    node.widgets?.find(w => w.name === "tag / revision")?.value = tag;
                }
                
                // Position near center of view
                node.pos = [
                    app.canvas.graph_mouse[0] + 50,
                    app.canvas.graph_mouse[1] + 50
                ];
                
                this.showToast("KumihoLoadAsset node created", "success");
            }
        }
    }
    
    getLineageTabHTML() {
        return `
            <div class="kumiho-lineage-input">
                <input type="text" id="kumiho-lineage-kref" placeholder="Enter kref URI...">
                <button id="kumiho-lineage-search">View</button>
            </div>
            
            <div class="kumiho-lineage-view" id="kumiho-lineage-view">
                <div class="kumiho-placeholder">
                    <p>Enter a kref URI to view its lineage</p>
                </div>
            </div>
            
            <div class="kumiho-lineage-stats" id="kumiho-lineage-stats">
                <!-- Stats will be populated when viewing lineage -->
            </div>
        `;
    }
    
    getSettingsTabHTML() {
        return `
            <div class="kumiho-settings-form">
                <div class="kumiho-setting">
                    <label for="kumiho-api-endpoint">API Endpoint</label>
                    <input type="text" id="kumiho-api-endpoint" 
                           value="${kumihoSettings.get('apiEndpoint')}">
                </div>
                
                <div class="kumiho-setting">
                    <label>
                        <input type="checkbox" id="kumiho-auto-register" 
                               ${kumihoSettings.get('autoRegister') ? 'checked' : ''}>
                        Auto-register dependencies
                    </label>
                </div>
                
                <div class="kumiho-setting">
                    <label>
                        <input type="checkbox" id="kumiho-create-lineage"
                               ${kumihoSettings.get('createLineage') ? 'checked' : ''}>
                        Create lineage edges
                    </label>
                </div>
                
                <div class="kumiho-setting">
                    <label>
                        <input type="checkbox" id="kumiho-show-notifications"
                               ${kumihoSettings.get('showNotifications') ? 'checked' : ''}>
                        Show notifications
                    </label>
                </div>
                
                <button id="kumiho-save-settings" class="kumiho-btn-primary">
                    Save Settings
                </button>
            </div>
        `;
    }
    
    addStyles() {
        if (document.getElementById("kumiho-styles")) return;
        
        const style = document.createElement("style");
        style.id = "kumiho-styles";
        style.textContent = `
            .kumiho-sidebar {
                position: fixed;
                right: -320px;
                top: 0;
                width: 320px;
                height: 100vh;
                background: ${KUMIHO_COLORS.background};
                border-left: 2px solid ${KUMIHO_COLORS.primary};
                z-index: 10000;
                display: flex;
                flex-direction: column;
                transition: right 0.3s ease;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                color: #fff;
            }
            
            .kumiho-sidebar.open {
                right: 0;
            }
            
            .kumiho-sidebar-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 12px 16px;
                background: ${KUMIHO_COLORS.secondary};
                border-bottom: 1px solid ${KUMIHO_COLORS.primary};
            }
            
            .kumiho-logo {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            
            .kumiho-emoji {
                font-size: 24px;
            }
            
            .kumiho-title {
                font-weight: bold;
                font-size: 16px;
                color: ${KUMIHO_COLORS.primary};
            }
            
            .kumiho-close-btn {
                background: none;
                border: none;
                color: #fff;
                font-size: 24px;
                cursor: pointer;
                padding: 0 8px;
            }
            
            .kumiho-close-btn:hover {
                color: ${KUMIHO_COLORS.primary};
            }
            
            .kumiho-tabs {
                display: flex;
                background: ${KUMIHO_COLORS.secondary};
                border-bottom: 1px solid #333;
            }
            
            .kumiho-tab {
                flex: 1;
                padding: 10px;
                background: none;
                border: none;
                color: #888;
                cursor: pointer;
                font-size: 13px;
                transition: all 0.2s;
            }
            
            .kumiho-tab:hover {
                color: #fff;
            }
            
            .kumiho-tab.active {
                color: ${KUMIHO_COLORS.primary};
                border-bottom: 2px solid ${KUMIHO_COLORS.primary};
            }
            
            .kumiho-tab-content {
                flex: 1;
                overflow-y: auto;
                padding: 16px;
            }
            
            .kumiho-search {
                display: flex;
                gap: 8px;
                margin-bottom: 12px;
            }
            
            .kumiho-search input {
                flex: 1;
                padding: 8px 12px;
                background: #1a1a2e;
                border: 1px solid #333;
                border-radius: 4px;
                color: #fff;
                font-size: 13px;
            }
            
            .kumiho-search input:focus {
                border-color: ${KUMIHO_COLORS.primary};
                outline: none;
            }
            
            .kumiho-search button {
                padding: 8px 12px;
                background: ${KUMIHO_COLORS.primary};
                border: none;
                border-radius: 4px;
                cursor: pointer;
            }
            
            .kumiho-filters {
                margin-bottom: 12px;
            }
            
            .kumiho-filters select {
                width: 100%;
                padding: 8px;
                background: #1a1a2e;
                border: 1px solid #333;
                border-radius: 4px;
                color: #fff;
                font-size: 13px;
                margin-bottom: 8px;
            }
            
            .kumiho-filters select:focus {
                border-color: ${KUMIHO_COLORS.primary};
                outline: none;
            }
            
            .kumiho-asset-list {
                min-height: 200px;
                max-height: 350px;
                overflow-y: auto;
                background: #1a1a2e;
                border-radius: 4px;
                padding: 12px;
                margin-bottom: 16px;
            }
            
            .kumiho-asset-item {
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                padding: 10px;
                background: #0d1b2a;
                border-radius: 4px;
                margin-bottom: 8px;
                border: 1px solid #333;
                transition: border-color 0.2s;
            }
            
            .kumiho-asset-item:hover {
                border-color: ${KUMIHO_COLORS.primary};
            }
            
            .kumiho-asset-name {
                font-weight: 500;
                color: #fff;
                flex: 1;
                min-width: 60%;
            }
            
            .kumiho-asset-kref,
            .kumiho-asset-path {
                font-size: 10px;
                color: #666;
                word-break: break-all;
                width: 100%;
                margin-top: 4px;
            }
            
            .kumiho-asset-kind {
                font-size: 11px;
                color: ${KUMIHO_COLORS.primary};
                background: rgba(255, 107, 53, 0.15);
                padding: 2px 6px;
                border-radius: 3px;
                margin-left: auto;
            }
            
            .kumiho-copy-btn,
            .kumiho-use-btn {
                padding: 4px 8px;
                margin-left: 4px;
                background: transparent;
                border: 1px solid #444;
                border-radius: 3px;
                cursor: pointer;
                font-size: 12px;
            }
            
            .kumiho-copy-btn:hover,
            .kumiho-use-btn:hover {
                background: ${KUMIHO_COLORS.primary};
                border-color: ${KUMIHO_COLORS.primary};
            }
            
            .kumiho-btn-secondary {
                background: transparent;
                color: ${KUMIHO_COLORS.primary};
                border: 1px solid ${KUMIHO_COLORS.primary};
                padding: 8px 16px;
                border-radius: 4px;
                cursor: pointer;
                font-size: 13px;
                margin-top: 12px;
            }
            
            .kumiho-btn-secondary:hover {
                background: rgba(255, 107, 53, 0.15);
            }
            
            .kumiho-loading {
                text-align: center;
                padding: 24px;
                color: ${KUMIHO_COLORS.primary};
            }
            
            .kumiho-placeholder {
                text-align: center;
                padding: 24px;
                color: #888;
            }
            
            .kumiho-btn-primary {
                background: ${KUMIHO_COLORS.primary};
                color: #fff;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                cursor: pointer;
                font-size: 14px;
                margin-top: 12px;
            }
            
            .kumiho-btn-primary:hover {
                background: #ff8555;
            }
            
            .kumiho-recent h4 {
                margin: 0 0 8px 0;
                font-size: 14px;
                color: #888;
            }
            
            .kumiho-empty {
                color: #555;
                font-size: 12px;
                font-style: italic;
            }
            
            .kumiho-setting {
                margin-bottom: 16px;
            }
            
            .kumiho-setting label {
                display: block;
                margin-bottom: 6px;
                font-size: 13px;
                color: #aaa;
            }
            
            .kumiho-setting input[type="text"] {
                width: 100%;
                padding: 8px 12px;
                background: #1a1a2e;
                border: 1px solid #333;
                border-radius: 4px;
                color: #fff;
                font-size: 13px;
                box-sizing: border-box;
            }
            
            .kumiho-setting input[type="checkbox"] {
                margin-right: 8px;
            }

            .kumiho-project-group {
                display: flex;
                flex-direction: column;
                gap: 8px;
            }

            .kumiho-radio-row {
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 13px;
                color: #ccc;
            }

            .kumiho-project-existing {
                display: flex;
                gap: 8px;
                align-items: center;
            }

            .kumiho-project-existing select {
                flex: 1;
                padding: 8px 10px;
                background: #1a1a2e;
                border: 1px solid #333;
                border-radius: 4px;
                color: #fff;
                font-size: 13px;
            }

            .kumiho-project-preview {
                font-size: 12px;
                color: #aaa;
                background: #111827;
                padding: 6px 8px;
                border: 1px dashed #333;
                border-radius: 4px;
            }
            
            .kumiho-footer {
                padding: 12px 16px;
                background: ${KUMIHO_COLORS.secondary};
                border-top: 1px solid #333;
                display: flex;
                justify-content: space-between;
                font-size: 11px;
                color: #888;
            }
            
            .kumiho-footer a {
                color: ${KUMIHO_COLORS.primary};
                text-decoration: none;
            }
            
            /* Toggle button */
            .kumiho-toggle-btn {
                position: fixed;
                right: 16px;
                top: 50%;
                transform: translateY(-50%);
                background: ${KUMIHO_COLORS.secondary};
                border: 2px solid ${KUMIHO_COLORS.primary};
                border-radius: 8px 0 0 8px;
                padding: 12px 8px;
                cursor: pointer;
                z-index: 9999;
                transition: all 0.3s;
            }
            
            .kumiho-toggle-btn:hover {
                background: ${KUMIHO_COLORS.primary};
            }
            
            .kumiho-toggle-btn.hidden {
                right: 320px;
            }
            
            /* Recent registration item */
            .kumiho-recent-item {
                padding: 8px;
                background: #1a1a2e;
                border-radius: 4px;
                margin-bottom: 8px;
                font-size: 12px;
            }
            
            .kumiho-recent-item .kref {
                color: ${KUMIHO_COLORS.primary};
                word-break: break-all;
            }
            
            .kumiho-recent-item .time {
                color: #666;
                font-size: 10px;
            }
            
            /* Notification toast */
            .kumiho-toast {
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: ${KUMIHO_COLORS.secondary};
                border: 1px solid ${KUMIHO_COLORS.primary};
                border-radius: 8px;
                padding: 16px;
                z-index: 10001;
                max-width: 300px;
                animation: slideIn 0.3s ease;
            }
            
            .kumiho-toast.success {
                border-color: ${KUMIHO_COLORS.success};
            }
            
            .kumiho-toast.error {
                border-color: ${KUMIHO_COLORS.error};
            }
            
            @keyframes slideIn {
                from {
                    opacity: 0;
                    transform: translateX(100px);
                }
                to {
                    opacity: 1;
                    transform: translateX(0);
                }
            }
        `;
        document.head.appendChild(style);
    }
    
    setupEventListeners() {
        // Close button
        document.getElementById("kumiho-close").addEventListener("click", () => {
            this.toggle();
        });
        
        // Tab switching
        this.container.querySelectorAll(".kumiho-tab").forEach(tab => {
            tab.addEventListener("click", (e) => {
                this.switchTab(e.target.dataset.tab);
            });
        });
        
        // Initialize assets tab on first load
        this.initAssetsTab();
    }
    
    switchTab(tabName) {
        this.currentTab = tabName;
        
        // Update tab buttons
        this.container.querySelectorAll(".kumiho-tab").forEach(tab => {
            tab.classList.toggle("active", tab.dataset.tab === tabName);
        });
        
        // Update content
        const content = document.getElementById("kumiho-tab-content");
        if (tabName === "assets") {
            content.innerHTML = this.getAssetsTabHTML();
            this.initAssetsTab();
        } else if (tabName === "lineage") {
            content.innerHTML = this.getLineageTabHTML();
        } else if (tabName === "settings") {
            this.initSettingsTab();
        }
    }

    async initSettingsTab() {
        const content = document.getElementById("kumiho-tab-content");
        content.innerHTML = this.getSettingsTabHTML();
        this.setupSettingsListeners();
    }
    
    setupSettingsListeners() {
        document.getElementById("kumiho-save-settings")?.addEventListener("click", () => {
            kumihoSettings.set("apiEndpoint", document.getElementById("kumiho-api-endpoint").value);
            kumihoSettings.set("autoRegister", document.getElementById("kumiho-auto-register").checked);
            kumihoSettings.set("createLineage", document.getElementById("kumiho-create-lineage").checked);
            kumihoSettings.set("showNotifications", document.getElementById("kumiho-show-notifications").checked);
            this.showToast("Settings saved!", "success");
        });
    }
    
    toggle() {
        this.isOpen = !this.isOpen;
        this.container.classList.toggle("open", this.isOpen);
        document.querySelector(".kumiho-toggle-btn")?.classList.toggle("hidden", this.isOpen);
    }
    
    addRecentRegistration(kref, deps, edges) {
        const list = document.getElementById("kumiho-recent-list");
        if (!list) return;
        
        // Remove empty placeholder
        const empty = list.querySelector(".kumiho-empty");
        if (empty) empty.remove();
        
        // Add new item
        const item = document.createElement("div");
        item.className = "kumiho-recent-item";
        item.innerHTML = `
            <div class="kref">${kref}</div>
            <div class="time">${new Date().toLocaleTimeString()} • ${deps} deps • ${edges} edges</div>
        `;
        list.insertBefore(item, list.firstChild);
        
        // Keep only last 5
        while (list.children.length > 5) {
            list.removeChild(list.lastChild);
        }
    }
    
    showToast(message, type = "info") {
        if (!kumihoSettings.get("showNotifications")) return;
        
        const toast = document.createElement("div");
        toast.className = `kumiho-toast ${type}`;
        toast.innerHTML = `
            <div style="display: flex; align-items: center; gap: 8px;">
                <span class="kumiho-emoji">🦊</span>
                <span>${message}</span>
            </div>
        `;
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.style.animation = "slideIn 0.3s ease reverse";
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }
}

// Create global instance
const kumihoSidebar = new KumihoSidebarPanel();

// =============================================================================
// Toggle Button
// =============================================================================

function createToggleButton() {
    const btn = document.createElement("button");
    btn.className = "kumiho-toggle-btn";
    btn.innerHTML = "🦊";
    btn.title = "Open Kumiho Panel";
    btn.addEventListener("click", () => kumihoSidebar.toggle());
    document.body.appendChild(btn);
}

// =============================================================================
// Register Extension
// =============================================================================

app.registerExtension({
    name: "kumiho.comfyui",
    
    async setup() {
        console.log("🦊 Kumiho ComfyUI Extension loaded (v" + KUMIHO_VERSION + ")");
        
        // Create UI elements
        kumihoSidebar.create();
        createToggleButton();
        
        // Listen for registration completion
        app.api.addEventListener("kumiho.register.complete", (event) => {
            const { kref, dependencies_count, edges_count } = event.detail;
            console.log(`🦊 Kumiho registered: ${kref}`);
            kumihoSidebar.addRecentRegistration(kref, dependencies_count, edges_count);
            kumihoSidebar.showToast(`Registered: ${kref}`, "success");
        });
        
        // Listen for general notifications
        app.api.addEventListener("kumiho.notification", (event) => {
            const { type, message, title } = event.detail;
            kumihoSidebar.showToast(message, type);
        });
        
        // Listen for asset load completion
        app.api.addEventListener("kumiho.asset.loaded", (event) => {
            const { kref, revision, status } = event.detail;
            console.log(`🦊 Kumiho asset loaded: ${kref} (revision: ${revision})`);
        });
        
        // Listen for errors
        app.api.addEventListener("kumiho.error", (event) => {
            const { message } = event.detail;
            kumihoSidebar.showToast(message, "error");
        });
    },
    
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        // Add custom behavior to Kumiho nodes
        if (nodeData.name.startsWith("Kumiho")) {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            
            nodeType.prototype.onNodeCreated = function() {
                if (onNodeCreated) {
                    onNodeCreated.apply(this, arguments);
                }
                
                // Add Kumiho branding/styling
                this.color = KUMIHO_COLORS.secondary;
                this.bgcolor = KUMIHO_COLORS.background;
                
                // Add custom context menu items
                const getExtraMenuOptions = this.getExtraMenuOptions;
                this.getExtraMenuOptions = function(_, options) {
                    if (getExtraMenuOptions) {
                        getExtraMenuOptions.apply(this, arguments);
                    }
                    
                    options.push(null); // Separator
                    options.push({
                        content: "🦊 Open Kumiho Panel",
                        callback: () => {
                            if (!kumihoSidebar.isOpen) {
                                kumihoSidebar.toggle();
                            }
                        }
                    });
                    options.push({
                        content: "📊 View Lineage",
                        callback: () => {
                            kumihoSidebar.toggle();
                            kumihoSidebar.switchTab("lineage");
                        }
                    });
                    options.push({
                        content: "📚 Kumiho Documentation",
                        callback: () => {
                            window.open("https://docs.kumiho.io", "_blank");
                        }
                    });
                };
            };
        }
    },
    
    nodeCreated(node, app) {
        // Special handling for KumihoRegister node
        if (node.type === "KumihoRegister") {
            // Add output preview
            const originalOnExecuted = node.onExecuted;
            node.onExecuted = function(output) {
                if (originalOnExecuted) {
                    originalOnExecuted.apply(this, arguments);
                }
                
                if (output && output.revision_kref) {
                    kumihoSidebar.showToast(`Registered: ${output.revision_kref[0]}`, "success");
                }
            };
        }
    }
});

// Export for external use
window.KumihoComfyUI = {
    version: KUMIHO_VERSION,
    settings: kumihoSettings,
    sidebar: kumihoSidebar,
};
