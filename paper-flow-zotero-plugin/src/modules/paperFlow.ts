import { config } from "../../package.json";
import { getString } from "../utils/locale";
import { getPref, setPref } from "../utils/prefs";

type NotifyExtra = { [key: string]: any };
type ProcessMode = "auto" | "manual";
type SyncMode = "with-ai" | "metadata-only";
type ExecutionMode = "auto" | "native" | "wsl";
type RuntimeMode = "wsl" | "native-unix" | "native-windows";
type GuideLanguage = "zh-CN" | "en" | "ja" | "ko" | "fr" | "de" | "es";

const pendingTimers = new Map<string, number>();
let pendingDeleteTimer: number | undefined;
// Serializes every spawned CLI run so a bulk import or fast edits never launch
// dozens of AI processes at once (Notion rate limits, local CPU exhaustion).
let runChain: Promise<void> = Promise.resolve();

const DEFAULT_PROCESS_TIMEOUT_MS = 30 * 60 * 1000;
const PAPER_FLOW_LINK_TITLE = "Paper Flow Notion";
const PAPER_FLOW_GUIDE_LINK_TITLE = "Paper Flow Guide";
const PAPER_FLOW_TAG = "paper-flow";
const PAPER_FLOW_DONE_TAG = "paper-flow:done";
const PAPER_FLOW_ERROR_TAG = "paper-flow:error";

const LANGUAGE_NAMES: Record<GuideLanguage, string> = {
  "zh-CN": "Simplified Chinese",
  en: "clear English",
  ja: "Japanese",
  ko: "Korean",
  fr: "French",
  de: "German",
  es: "Spanish",
};

const DEFAULT_READING_PROMPTS: Partial<Record<GuideLanguage, string>> = {
  "zh-CN": `用中文大白话解读这篇论文，不要全文翻译。

目标：读者只看这份文本，就能把握全文的思考逻辑、理论机制、实验设计、主要数据结论和局限。

写法要求：
- 准确、精简、像研究组师兄师姐讲论文，不要宣传语。
- 优先说明：这篇论文面向什么问题；该领域相关工作有哪些路线、怎么分类；本文相对它们的优点是什么；核心理论/机制怎么理解；做了哪些实验；得到什么结论和数据；还存在什么问题。
- 如果能从正文、caption 或表格文本中读到关键信息，请专门解释关键 figure/table 在证明什么；如果提取文本看不到图表细节，就明确说不要编造。
- 不要写“为什么值得读”“阅读时追问的问题”“阅读计划”这类空话。
- 不要逐节翻译，不要复述摘要，不要把全文从头到尾改写一遍。
- 如果 Zotero 元数据缺失，请尽量从 PDF/正文中推断标题、作者和年份。
- 全部解释字段使用简体中文，控制在 10 分钟内读完，优先高信息密度。`,
  en: `Explain this paper in clear, compact English. Do not translate the paper section by section.

Goal: the reader should understand the paper's reasoning, theoretical mechanism, experiment design, main numerical conclusions, and limitations from this guide alone.

Writing requirements:
- Be accurate, concise, and practical, like a senior labmate explaining the paper.
- Prioritize: the problem this paper addresses; how related work in the field can be grouped; what advantage this paper claims over those lines; how the core theory/mechanism works; what experiments were run; what conclusions and numbers matter; what problems remain.
- If useful figure/table information is visible in the extracted text, captions, or table text, explain what the key figure/table demonstrates. If visual details are not visible from extraction, say so instead of inventing them.
- Do not include generic sections like "why it is worth reading", "questions to ask while reading", or "reading plan".
- Do not restate the abstract or rewrite the whole paper from beginning to end.
- If Zotero metadata is missing, infer title, authors, and year from the PDF text when possible.
- Keep the explanation readable in under 10 minutes and prioritize information density.`,
};

function getDefaultReadingPrompt(language: GuideLanguage) {
  const prompt = DEFAULT_READING_PROMPTS[language];
  if (prompt) {
    return prompt;
  }
  const languageName = LANGUAGE_NAMES[language];
  return `Explain this paper in ${languageName}, not as a full-text translation.

Goal: after reading this guide, the reader should understand the paper's problem, research logic, theory or mechanism, experiments, main data-backed conclusions, and limitations.

Writing requirements:
- Write all explanatory content in ${languageName}.
- Be accurate, concise, and concrete. Write like a senior labmate explaining the paper.
- Prioritize: the problem being solved; how related work can be grouped; how this paper differs; the core mechanism/theory; what experiments were run; what conclusions and numbers support the claims; what remains weak or questionable.
- Do not write generic sections such as "why it is worth reading", "questions to ask while reading", or a reading plan.
- Do not translate section by section, do not restate the abstract, and do not paraphrase the whole paper end to end.
- If Zotero metadata is missing, infer title, authors, and year from the PDF/body when possible.
- Explain high-value figures/tables when captions or extracted table text are available. If visual content is not available from extraction, say that instead of inventing details.
- Keep the guide readable in under 10 minutes.`;
}

export class PaperFlowPlugin {
  static registerPrefs() {
    const registered = Zotero.PreferencePanes.register({
      pluginID: addon.data.config.addonID,
      src: rootURI + "content/preferences.xhtml",
      label: getString("prefs-title"),
      image: `chrome://${addon.data.config.addonRef}/content/icons/favicon.png`,
    });
    // register() resolves to the pane ID; keep it so the Tools menu entry can
    // jump straight to this pane.
    void Promise.resolve(registered as unknown as string).then((paneID) => {
      if (paneID) {
        addon.data.runtime.prefPaneID = String(paneID);
      }
    });
  }

  /**
   * Removes any existing DOM nodes with the given menu id from all main
   * windows. Older plugin versions leaked their menu items on reinstall
   * (their shutdown unregistered a recreated toolkit instance that had never
   * registered anything), so sweeping before registering also heals
   * duplicates left behind by an upgrade-from-old-version.
   */
  private static removeStaleMenuNodes(id: string) {
    for (const win of Zotero.getMainWindows()) {
      win.document
        .querySelectorAll(`[id="${id}"]`)
        .forEach((node: Element) => node.remove());
    }
  }

  /** Adds a "Paper Flow Settings" shortcut to the Tools menu. */
  static registerToolsMenu() {
    this.removeStaleMenuNodes("zotero-tools-paperflow-settings");
    ztoolkit.Menu.register("menuTools", {
      tag: "menuitem",
      id: "zotero-tools-paperflow-settings",
      label: getString("menuitem-settings"),
      commandListener: () => {
        this.openSettingsPane();
      },
      icon: `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.png`,
    });
  }

  static openSettingsPane() {
    const paneID = addon.data.runtime.prefPaneID;
    const utils = Zotero.Utilities.Internal as any;
    if (typeof utils?.openPreferences === "function") {
      utils.openPreferences(paneID);
      return;
    }
    // Fallback for builds without openPreferences: open the dialog generically.
    (Zotero.getMainWindow() as any)?.openDialog?.(
      "chrome://zotero/content/preferences/preferences.xhtml",
      "zotero-prefs",
      "chrome,titlebar,toolbar,centerscreen",
      paneID ? { pane: paneID } : undefined,
    );
  }

  static registerNotifier() {
    if (addon.data.runtime.notifierID) {
      return;
    }

    const callback = {
      notify: async (
        event: string,
        type: string,
        ids: Array<string | number>,
        extraData: NotifyExtra,
      ) => {
        if (!addon.data.alive) {
          return;
        }
        await addon.hooks.onNotify(event, type, ids, extraData);
      },
    };

    addon.data.runtime.notifierID = Zotero.Notifier.registerObserver(callback, [
      "item",
      "collection-item",
    ]);
  }

  static unregisterNotifier() {
    if (!addon.data.runtime.notifierID) {
      return;
    }
    Zotero.Notifier.unregisterObserver(addon.data.runtime.notifierID);
    addon.data.runtime.notifierID = undefined;
  }

  /**
   * Clears every pending debounce timer. Called on shutdown so queued work can
   * never fire after the plugin is disabled.
   */
  static dispose() {
    for (const handle of pendingTimers.values()) {
      this.clearManagedTimeout(handle);
    }
    pendingTimers.clear();
    if (typeof pendingDeleteTimer === "number") {
      this.clearManagedTimeout(pendingDeleteTimer);
      pendingDeleteTimer = undefined;
    }
  }

  // Timers must come from a real window: the plugin sandbox loaded by
  // bootstrap.js (Services.scriptloader.loadSubScript) has no global
  // setTimeout/clearTimeout, so a bare call would throw ReferenceError and
  // silently break auto-sync.
  private static setManagedTimeout(callback: () => void, ms: number): number {
    const win = Zotero.getMainWindow();
    if (win?.setTimeout) {
      return win.setTimeout(callback, ms) as unknown as number;
    }
    return (ztoolkit.getGlobal("setTimeout") as any)(callback, ms);
  }

  private static clearManagedTimeout(handle: number | undefined) {
    if (typeof handle !== "number") {
      return;
    }
    const win = Zotero.getMainWindow();
    if (win?.clearTimeout) {
      win.clearTimeout(handle);
      return;
    }
    (ztoolkit.getGlobal("clearTimeout") as any)(handle);
  }

  /**
   * Runs CLI work one task at a time. A rejected task does not break the chain.
   */
  private static enqueueRun(task: () => Promise<void>): Promise<void> {
    runChain = runChain.then(task, task);
    return runChain;
  }

  static registerMenus() {
    this.removeStaleMenuNodes("zotero-itemmenu-paperflow-process");
    this.removeStaleMenuNodes("zotero-itemmenu-paperflow-metadata");
    ztoolkit.Menu.register("item", {
      tag: "menuitem",
      id: "zotero-itemmenu-paperflow-process",
      label: "Paper Flow: Sync + AI guide",
      commandListener: async () => {
        await this.processSelectedItems("manual", "with-ai");
      },
      icon: `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.png`,
    });
    ztoolkit.Menu.register("item", {
      tag: "menuitem",
      id: "zotero-itemmenu-paperflow-metadata",
      label: "Paper Flow: Sync metadata only",
      commandListener: async () => {
        await this.processSelectedItems("manual", "metadata-only");
      },
      icon: `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.png`,
    });
    ztoolkit.Menu.register("item", {
      tag: "menuitem",
      id: "zotero-itemmenu-paperflow-open-page",
      label: "Paper Flow: Open Notion page",
      commandListener: async () => {
        await this.openSelectedPaperFlowLink(PAPER_FLOW_LINK_TITLE);
      },
      icon: `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.png`,
    });
    ztoolkit.Menu.register("item", {
      tag: "menuitem",
      id: "zotero-itemmenu-paperflow-open-guide",
      label: "Paper Flow: Open Reading Guide",
      commandListener: async () => {
        await this.openSelectedPaperFlowLink(PAPER_FLOW_GUIDE_LINK_TITLE);
      },
      icon: `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.png`,
    });
    ztoolkit.Menu.register("collection", {
      tag: "menuitem",
      id: "zotero-collectionmenu-paperflow-sync",
      label: "Paper Flow: Sync collection + AI",
      commandListener: async () => {
        await this.processSelectedCollection("with-ai");
      },
      icon: `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.png`,
    });
    ztoolkit.Menu.register("collection", {
      tag: "menuitem",
      id: "zotero-collectionmenu-paperflow-metadata",
      label: "Paper Flow: Sync collection metadata only",
      commandListener: async () => {
        await this.processSelectedCollection("metadata-only");
      },
      icon: `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.png`,
    });
  }

  static async handleNotify(
    event: string,
    type: string,
    ids: Array<string | number>,
    _extraData: NotifyExtra,
  ) {
    // Roaming-config adoption runs regardless of the auto-sync toggle: a
    // config note arriving via Zotero sync should apply immediately.
    if (type === "item" && ["add", "modify"].includes(event)) {
      void this.adoptIncomingConfigNote(ids);
    }
    if (!this.isEnabled() || !this.isAutoSyncEnabled()) {
      return;
    }
    if (type === "collection-item" && event === "add") {
      await this.handleCollectionItemsAdded(ids);
      return;
    }
    if (type !== "item") {
      return;
    }
    if (event === "delete") {
      this.queueDeletePrune();
      return;
    }
    if (!["add", "modify"].includes(event)) {
      return;
    }

    const items = await Zotero.Items.getAsync(ids as number[]);
    for (const rawItem of items) {
      const item = this.normalizeItem(rawItem, "auto");
      if (!item) {
        continue;
      }
      if (!this.matchesCollection(item)) {
        continue;
      }
      // A "modify" event whose bibliographic content is unchanged is usually
      // a sync metadata write-back, not a real paper-content edit.
      if (event === "modify" && !(await this.hasItemContentChanged(item))) {
        this.setStatusMessage(
          `Skipped ${this.getItemLabel(item)}: content unchanged since the last Paper Flow run (likely a sync metadata write-back).`,
        );
        continue;
      }
      this.queueItem(item, event);
    }
  }

  private static async handleCollectionItemsAdded(ids: Array<string | number>) {
    const itemIDs = ids
      .map((id) => this.itemIDFromCollectionItemID(id))
      .filter((id): id is number => typeof id === "number");
    if (!itemIDs.length) {
      this.setStatusMessage(
        `Paper Flow ignored collection-item.add because no item IDs were found: ${ids.join(", ")}`,
      );
      return;
    }

    const items = await Zotero.Items.getAsync(itemIDs);
    for (const rawItem of items) {
      const item = this.normalizeItem(rawItem, "auto");
      if (!item) {
        continue;
      }
      if (!this.matchesCollection(item)) {
        this.setStatusMessage(
          `Paper Flow skipped ${this.getItemLabel(item)} because it is outside the watched collection.`,
        );
        continue;
      }
      this.queueItem(item, "collection-item.add");
    }
  }

  private static itemIDFromCollectionItemID(id: string | number) {
    if (typeof id === "number") {
      return id;
    }
    const parts = String(id)
      .split("-")
      .map((part) => Number(part));
    const itemID = parts[1] || parts[0];
    return Number.isFinite(itemID) ? itemID : null;
  }

  static async processSelectedItems(
    mode: ProcessMode,
    syncMode: SyncMode = "with-ai",
  ) {
    const selected = this.getSelectedItems();
    if (!selected.length) {
      this.setStatusMessage("No Zotero item is selected.");
      return;
    }

    const uniqueItems = new Map<string, Zotero.Item>();
    for (const rawItem of selected) {
      const item = this.normalizeItem(rawItem, mode);
      if (!item) {
        continue;
      }
      uniqueItems.set(item.key, item);
    }

    if (!uniqueItems.size) {
      this.setStatusMessage(
        "The current selection does not include a regular item or a standalone PDF that Paper Flow can process.",
      );
      return;
    }

    for (const item of uniqueItems.values()) {
      await this.enqueueRun(() =>
        this.processItemNow(item, mode, "manual", syncMode),
      );
    }
  }

  static async processSelectedCollection(syncMode: SyncMode) {
    const collection = this.getSelectedCollection();
    if (!collection) {
      this.setStatusMessage("No Zotero collection is selected.");
      return;
    }
    await this.enqueueRun(() =>
      this.processCollectionNow(collection, syncMode),
    );
  }

  static async pruneDeletedItems(mode: ProcessMode, dryRun?: boolean) {
    const preview = dryRun ?? Boolean(getPref("archiveDeletedWithPreview"));
    const commandTemplate = await this.getDeleteCommandTemplate(preview);
    if (!commandTemplate) {
      this.setStatusMessage(
        "Paper Flow delete command is empty. Configure managed runtime settings or provide a custom delete command template.",
      );
      return;
    }

    const message = preview
      ? `Previewing deleted-item cleanup (${mode}).`
      : `Running deleted-item cleanup (${mode}).`;
    this.setStatusMessage(message);
    if (this.shouldShowNotifications()) {
      this.showProgress(message, "default");
    }

    try {
      const output = await this.executeShellCommandWithOutput(commandTemplate);
      const done = await this.buildDiagnosticMessage({
        title: "Deleted-item cleanup completed.",
        command: commandTemplate,
        output: output.trim() || "(no output)",
      });
      this.setStatusMessage(done);
      if (this.shouldShowNotifications()) {
        this.showProgress(done, "success");
      }
    } catch (error) {
      const details = error instanceof Error ? error.message : String(error);
      const failure = `Deleted-item cleanup failed: ${details}`;
      this.setStatusMessage(failure);
      if (this.shouldShowNotifications()) {
        this.showProgress(failure, "error");
      }
    }
  }

  static async checkAiSetup() {
    const modeLabel = this.useCustomCommands() ? "CUSTOM" : "MANAGED";
    const selectedAi = this.getResolvedAiCommand();
    const runtime = this.getRuntimeMode();
    const workspacePath = this.resolveWorkspacePath(runtime);
    // Write the Python probe to a file so the command line stays short. Inlining
    // it via `python -c '<...>'` overflows cmd.exe's line limit on Windows/WSL.
    const pythonCheck = await this.writeRuntimeTempFile(
      "paper-flow-setup-check",
      "py",
      this.buildPythonRuntimeCheck(selectedAi),
    );
    const command = await this.buildSetupCheckCommand({
      modeLabel,
      runtime,
      selectedAi,
      workspacePath,
      pythonCheck,
    });

    try {
      const report = await this.executeShellCommandWithOutput(command);
      const message = await this.buildDiagnosticMessage({
        title: "Paper Flow setup check completed.",
        command,
        output: report.trim() || "(no output)",
      });
      this.setStatusMessage(message);
      if (this.shouldShowNotifications()) {
        this.showProgress("Paper Flow setup check completed.", "success");
      }
    } catch (error) {
      const details = error instanceof Error ? error.message : String(error);
      const message = await this.buildDiagnosticMessage({
        title: "Paper Flow setup check failed.",
        command,
        output: details,
      });
      this.setStatusMessage(message);
      this.copyTextToClipboard(message);
      if (this.shouldShowNotifications()) {
        this.showProgress("Paper Flow setup check failed.", "error");
      }
    }
  }

  static getExampleCommand(): string {
    // Readable preview of the managed command (placeholders kept, no temp file
    // written). The real run substitutes these and writes a script file.
    const composed = this.composeManagedProcessInner({
      key: "{{key}}",
      title: "{{title}}",
      collection: "{{collection}}",
      promptFile: "{{promptFile}}",
      promptFileWindows: "{{promptFileWindows}}",
      skipAi: false,
    });
    if (!composed) {
      return "";
    }
    return composed.full ?? composed.inner ?? "";
  }

  static setStatusMessage(message: string) {
    addon.data.runtime.lastStatus = message;
    addon.data.prefs?.updateStatus?.(message);
    ztoolkit.log(message);
  }

  static getStatusMessage(): string {
    return addon.data.runtime.lastStatus;
  }

  /** The built-in starter template (last-resort fallback). */
  static getDefaultReadingPrompt(): string {
    return getDefaultReadingPrompt(this.getGuideLanguage());
  }

  /**
   * What "Fill default prompt" should insert: the user's chosen default preset
   * if one is set, otherwise the built-in starter template.
   */
  static getActiveDefaultPrompt(): string {
    const name = this.getDefaultPromptName();
    if (name) {
      const preset = this.getPromptPresets().find((item) => item.name === name);
      if (preset) {
        return preset.text;
      }
    }
    return getDefaultReadingPrompt(this.getGuideLanguage());
  }

  // ---- Prompt presets -----------------------------------------------------
  // Saved prompts live in the `promptPresets` pref (JSON), which persists across
  // xpi updates. A chosen default is re-applied at every startup so the active
  // prompt survives upgrades, restarts, and stray user.js overrides.

  static readonly MAX_PROMPT_PRESETS = 10;

  static getPromptPresets(): Array<{ name: string; text: string }> {
    const raw = String(getPref("promptPresets") || "").trim();
    if (!raw) {
      return [];
    }
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed.filter(
        (preset) =>
          preset &&
          typeof preset.name === "string" &&
          typeof preset.text === "string",
      );
    } catch (_error) {
      return [];
    }
  }

  static getDefaultPromptName(): string {
    return String(getPref("defaultPromptName") || "");
  }

  /** Saves the given text as a new preset (deduped by content). Returns the name. */
  static savePromptPreset(text: string): string {
    const trimmed = String(text || "").trim();
    if (!trimmed) {
      return "";
    }
    const list = this.getPromptPresets();
    const existing = list.find((preset) => preset.text === trimmed);
    if (existing) {
      return existing.name;
    }
    const name = this.makePresetName(trimmed, list);
    list.unshift({ name, text: trimmed });
    setPref(
      "promptPresets",
      JSON.stringify(list.slice(0, this.MAX_PROMPT_PRESETS)),
    );
    return name;
  }

  private static makePresetName(
    text: string,
    list: Array<{ name: string }>,
  ): string {
    const firstLine = text.split(/\r?\n/)[0].trim();
    const base = (firstLine.slice(0, 30) || "Prompt").trim();
    const names = new Set(list.map((preset) => preset.name));
    if (!names.has(base)) {
      return base;
    }
    let index = 2;
    while (names.has(`${base} (${index})`)) {
      index += 1;
    }
    return `${base} (${index})`;
  }

  static deletePromptPreset(name: string) {
    const list = this.getPromptPresets().filter(
      (preset) => preset.name !== name,
    );
    setPref("promptPresets", JSON.stringify(list));
    if (this.getDefaultPromptName() === name) {
      setPref("defaultPromptName", "");
    }
  }

  /** Loads a preset's text as the active prompt. Returns the text, or null. */
  static applyPromptPreset(name: string): string | null {
    const preset = this.getPromptPresets().find((item) => item.name === name);
    if (!preset) {
      return null;
    }
    setPref("promptOverride", preset.text);
    return preset.text;
  }

  /** Marks a preset as the startup default and makes it active now. */
  static setDefaultPromptPreset(name: string): boolean {
    const preset = this.getPromptPresets().find((item) => item.name === name);
    if (!preset) {
      return false;
    }
    setPref("defaultPromptName", name);
    setPref("promptOverride", preset.text);
    return true;
  }

  /**
   * Re-applies the chosen default preset to the active prompt. Called at startup
   * so the default survives xpi updates and any user.js reset of promptOverride.
   */
  static applyDefaultPrompt() {
    const name = this.getDefaultPromptName();
    if (!name) {
      return;
    }
    const preset = this.getPromptPresets().find((item) => item.name === name);
    if (preset) {
      setPref("promptOverride", preset.text);
    }
  }

  static copyStatusToClipboard() {
    this.copyTextToClipboard(this.getStatusMessage());
    this.setStatusMessage(
      `${this.getStatusMessage()}\n\nCOPIED_TO_CLIPBOARD:OK`,
    );
  }

  private static isEnabled() {
    return Boolean(getPref("enabled"));
  }

  private static isAutoSyncEnabled() {
    return Boolean(getPref("autoSync"));
  }

  private static shouldShowNotifications() {
    return Boolean(getPref("showNotifications"));
  }

  private static useCustomCommands() {
    return Boolean(getPref("useCustomCommands"));
  }

  private static getSelectedItems() {
    const win = Zotero.getMainWindow();
    return win?.ZoteroPane?.getSelectedItems?.() || [];
  }

  private static normalizeItem(
    item: Zotero.Item | undefined,
    mode: ProcessMode,
  ): Zotero.Item | null {
    if (!item) {
      return null;
    }
    if (item.isRegularItem()) {
      return item;
    }
    if (!item.isAttachment() || !item.isPDFAttachment()) {
      return null;
    }

    const parentID = item.parentID;
    if (parentID) {
      if (mode === "manual") {
        return Zotero.Items.get(parentID) as Zotero.Item;
      }
      return null;
    }
    return item;
  }

  private static queueItem(item: Zotero.Item, reason: string) {
    const existing = pendingTimers.get(item.key);
    this.clearManagedTimeout(existing);

    const debounceMs = this.getDebounceMs();
    const handle = this.setManagedTimeout(() => {
      pendingTimers.delete(item.key);
      if (!addon.data.alive || !this.isEnabled() || !this.isAutoSyncEnabled()) {
        return;
      }
      void this.enqueueRun(() =>
        this.processItemNow(item, "auto", reason, "with-ai"),
      );
    }, debounceMs);
    pendingTimers.set(item.key, handle);
    this.setStatusMessage(
      `Queued ${this.getItemLabel(item)} after Zotero ${reason}. Waiting ${Math.round(debounceMs / 1000)}s.`,
    );
  }

  /**
   * Coalesces a burst of deletions into a single debounced prune run instead of
   * launching one full CLI process per deleted item.
   */
  private static queueDeletePrune() {
    this.clearManagedTimeout(pendingDeleteTimer);
    const debounceMs = this.getDebounceMs();
    pendingDeleteTimer = this.setManagedTimeout(() => {
      pendingDeleteTimer = undefined;
      if (!addon.data.alive || !this.isEnabled() || !this.isAutoSyncEnabled()) {
        return;
      }
      void this.enqueueRun(() => this.pruneDeletedItems("auto", true));
    }, debounceMs);
    this.setStatusMessage(
      `Detected a Zotero deletion. Will sync deletions to Notion in ${Math.round(debounceMs / 1000)}s.`,
    );
  }

  private static getDebounceMs() {
    const raw = String(getPref("debounceSeconds") || "20").trim();
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isFinite(parsed) || parsed < 0) {
      return 20_000;
    }
    return parsed * 1000;
  }

  private static matchesCollection(item: Zotero.Item) {
    const watched = String(getPref("watchedCollection") || "").trim();
    const watchedKeys = this.getWatchedCollectionKeys();
    if (!watched && !watchedKeys.length) {
      return true;
    }

    const collectionIDs = item.getCollections();
    const keys = collectionIDs
      .map((collectionID) => Zotero.Collections.get(collectionID))
      .filter(Boolean)
      .map((collection) => collection.key);
    if (watchedKeys.some((key) => keys.includes(key))) {
      return true;
    }
    const names = this.getCollectionNames(item).map((name) =>
      name.toLowerCase(),
    );
    return Boolean(watched) && names.includes(watched.toLowerCase());
  }

  private static getCollectionNames(item: Zotero.Item) {
    const collectionIDs = item.getCollections();
    return collectionIDs
      .map((collectionID) => Zotero.Collections.get(collectionID))
      .filter(Boolean)
      .map((collection) => collection.name);
  }

  private static getWatchedCollectionKeys() {
    return String(getPref("watchedCollectionKeys") || "")
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
  }

  private static getSelectedCollection(): Zotero.Collection | null {
    const win = Zotero.getMainWindow();
    return win?.ZoteroPane?.getSelectedCollection?.() || null;
  }

  private static dedupeEnabled() {
    return Boolean(getPref("skipUnchanged"));
  }

  /**
   * Returns true when the item's bibliographic content differs from the last
   * version Paper Flow processed (or when dedup is disabled / never seen).
   */
  private static async hasItemContentChanged(item: Zotero.Item) {
    if (!this.dedupeEnabled()) {
      return true;
    }
    const stored = this.getProcessedFingerprints()[item.key];
    if (!stored) {
      return true;
    }
    const current = await this.computeItemFingerprint(item);
    return stored !== current;
  }

  private static async rememberProcessedItem(item: Zotero.Item) {
    try {
      const fingerprint = await this.computeItemFingerprint(item);
      const map = this.getProcessedFingerprints();
      map[item.key] = fingerprint;
      // Soft cap so the stored map cannot grow without bound. JSON preserves
      // insertion order, so dropping the oldest keys is a reasonable FIFO.
      const keys = Object.keys(map);
      if (keys.length > 5000) {
        for (const key of keys.slice(0, keys.length - 4000)) {
          delete map[key];
        }
      }
      setPref("processedFingerprints", JSON.stringify(map));
    } catch (error) {
      ztoolkit.log(`Paper Flow could not store item fingerprint: ${error}`);
    }
  }

  private static getProcessedFingerprints(): Record<string, string> {
    const raw = String(getPref("processedFingerprints") || "").trim();
    if (!raw) {
      return {};
    }
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  /**
   * Builds a stable fingerprint from the fields that should trigger a re-read.
   * Deliberately excludes sync metadata (tags, related links, the Extra field,
   * date-modified), so a status write-back never looks like a real content
   * change.
   */
  private static async computeItemFingerprint(item: Zotero.Item) {
    const parts: string[] = [];
    if (item.isAttachment()) {
      parts.push("attachment");
      parts.push(item.key);
      parts.push(item.attachmentFilename || "");
    } else {
      parts.push("item");
      parts.push(String(item.itemTypeID || ""));
      parts.push(item.getField("title") || "");
      parts.push(
        (item.getCreators?.() || [])
          .map((creator) =>
            `${creator.firstName || ""} ${creator.lastName || ""}`.trim(),
          )
          .join("|"),
      );
      parts.push(item.getField("date") || "");
      parts.push(item.getField("DOI") || "");
      parts.push(item.getField("publicationTitle") || "");
      parts.push(item.getField("abstractNote") || "");
      try {
        const attachment = await item.getBestAttachment?.();
        if (attachment) {
          parts.push(attachment.key);
          parts.push(attachment.attachmentFilename || "");
        }
      } catch (_error) {
        // Best-attachment lookup is best-effort only.
      }
    }
    const joined = parts.join("");
    return `${joined.length.toString(16)}-${this.hashString(joined)}`;
  }

  /** FNV-1a 32-bit hash. Not cryptographic; only used for change detection. */
  private static hashString(value: string) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < value.length; i++) {
      hash ^= value.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  private static async processItemNow(
    item: Zotero.Item,
    mode: ProcessMode,
    reason: string,
    syncMode: SyncMode,
  ) {
    const promptFiles = await this.createPromptFiles();
    const command = await this.buildProcessCommand(item, promptFiles, syncMode);
    if (!command) {
      this.setStatusMessage(
        "Paper Flow command is empty. Configure managed runtime settings or provide a custom process command template.",
      );
      return;
    }
    const itemLabel = this.getItemLabel(item);

    this.setStatusMessage(
      `Running Paper Flow for ${itemLabel} (${mode}, ${reason}).`,
    );
    if (this.shouldShowNotifications()) {
      this.showProgress(`${config.addonName}: ${itemLabel}`, "default");
    }

    try {
      const output = await this.executeShellCommandWithOutput(command);
      await this.applySyncWriteback(item, output, syncMode);
      // Record what we just processed so later sync metadata write-backs are
      // recognised as "unchanged" and skipped.
      await this.rememberProcessedItem(item);
      const message = await this.buildDiagnosticMessage({
        title: `Finished processing ${itemLabel}.`,
        command,
        output: output.trim() || "(no output)",
      });
      this.setStatusMessage(message);
      if (this.shouldShowNotifications()) {
        this.showProgress(`Finished processing ${itemLabel}.`, "success");
      }
    } catch (error) {
      const details = error instanceof Error ? error.message : String(error);
      const message = await this.buildDiagnosticMessage({
        title: `Paper Flow failed for ${itemLabel}.`,
        command,
        output: details,
      });
      this.setStatusMessage(message);
      this.copyTextToClipboard(message);
      await this.applyFailureWriteback(item);
      if (this.shouldShowNotifications()) {
        this.showProgress(`Paper Flow failed for ${itemLabel}.`, "error");
      }
    }
  }

  private static async processCollectionNow(
    collection: Zotero.Collection,
    syncMode: SyncMode,
  ) {
    const promptFiles = await this.createPromptFiles();
    const command = await this.buildCollectionSyncCommand(
      collection,
      promptFiles,
      syncMode,
    );
    if (!command) {
      this.setStatusMessage(
        "Paper Flow collection command is empty. Set the workspace path first.",
      );
      return;
    }
    this.setStatusMessage(
      `Running Paper Flow for collection ${collection.name}.`,
    );
    try {
      const output = await this.executeShellCommandWithOutput(command);
      const message = await this.buildDiagnosticMessage({
        title: `Finished syncing collection ${collection.name}.`,
        command,
        output: output.trim() || "(no output)",
      });
      this.setStatusMessage(message);
      if (this.shouldShowNotifications()) {
        this.showProgress(
          `Finished syncing collection ${collection.name}.`,
          "success",
        );
      }
    } catch (error) {
      const details = error instanceof Error ? error.message : String(error);
      const message = await this.buildDiagnosticMessage({
        title: `Paper Flow collection sync failed for ${collection.name}.`,
        command,
        output: details,
      });
      this.setStatusMessage(message);
      this.copyTextToClipboard(message);
      if (this.shouldShowNotifications()) {
        this.showProgress(
          `Collection sync failed: ${collection.name}.`,
          "error",
        );
      }
    }
  }

  private static parseStructuredOutput(output: string) {
    const values: Record<string, string> = {};
    for (const line of output.split(/\r?\n/)) {
      const match = line.match(/^([A-Z0-9_]+):(.*)$/);
      if (!match) {
        continue;
      }
      values[match[1]] = match[2].trim();
    }
    return values;
  }

  private static async applySyncWriteback(
    item: Zotero.Item,
    output: string,
    syncMode: SyncMode,
  ) {
    const values = this.parseStructuredOutput(output);
    try {
      await this.updatePaperFlowTags(item, true);
      if (values.PAPER_PAGE_URL) {
        await this.upsertLinkedUrlAttachment(
          item,
          PAPER_FLOW_LINK_TITLE,
          values.PAPER_PAGE_URL,
        );
      }
      if (values.GUIDE_PAGE_URL && syncMode === "with-ai") {
        await this.upsertLinkedUrlAttachment(
          item,
          PAPER_FLOW_GUIDE_LINK_TITLE,
          values.GUIDE_PAGE_URL,
        );
      }
    } catch (error) {
      this.setStatusMessage(
        `${this.getStatusMessage()}\n\nZOTERO_WRITEBACK_WARNING:${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  private static async applyFailureWriteback(item: Zotero.Item) {
    try {
      await this.updatePaperFlowTags(item, false);
    } catch (error) {
      ztoolkit.log(`Paper Flow could not write failure tag: ${error}`);
    }
  }

  private static async updatePaperFlowTags(
    item: Zotero.Item,
    success: boolean,
  ) {
    item.addTag(PAPER_FLOW_TAG, 0);
    item.addTag(success ? PAPER_FLOW_DONE_TAG : PAPER_FLOW_ERROR_TAG, 0);
    item.removeTag(success ? PAPER_FLOW_ERROR_TAG : PAPER_FLOW_DONE_TAG);
    await item.saveTx();
  }

  private static async upsertLinkedUrlAttachment(
    parent: Zotero.Item,
    title: string,
    url: string,
  ) {
    const existing = this.findLinkedUrlAttachment(parent, title);
    if (existing) {
      existing.setField("title", title);
      (existing as any).attachmentPath = url;
      if (typeof (existing as any).setField === "function") {
        (existing as any).setField("url", url);
      }
      await existing.saveTx();
      return;
    }
    await (Zotero.Attachments as any).linkFromURL({
      url,
      parentItemID: parent.id,
      title,
      contentType: "text/html",
    });
  }

  private static findLinkedUrlAttachment(
    parent: Zotero.Item,
    title: string,
  ): Zotero.Item | null {
    const attachmentIDs = parent.getAttachments?.() || [];
    for (const id of attachmentIDs) {
      const attachment = Zotero.Items.get(id) as Zotero.Item | false;
      if (!attachment || !attachment.isAttachment?.()) {
        continue;
      }
      const linkMode = (attachment as any).attachmentLinkMode;
      const attachmentTitle =
        attachment.getField("title") || attachment.attachmentFilename || "";
      if (
        linkMode === Zotero.Attachments.LINK_MODE_LINKED_URL &&
        attachmentTitle === title
      ) {
        return attachment;
      }
    }
    return null;
  }

  private static async openSelectedPaperFlowLink(title: string) {
    const selected = this.getSelectedItems();
    for (const rawItem of selected) {
      const item = this.normalizeItem(rawItem, "manual");
      if (!item) {
        continue;
      }
      const attachment = this.findLinkedUrlAttachment(item, title);
      const url = (attachment as any)?.attachmentPath;
      if (url) {
        (Zotero as any).launchURL(url);
        return;
      }
    }
    this.setStatusMessage(
      `No ${title} link attachment found for the selected item.`,
    );
  }

  /**
   * Writes content to a temp file and returns its path in every form a runtime
   * might need. Used to keep large payloads (prompts, the Python setup-check
   * script) OUT of the command line, which otherwise overflows cmd.exe's
   * ~8191-character limit on Windows/WSL and silently truncates the command.
   */
  private static async writeRuntimeTempFile(
    prefix: string,
    extension: string,
    content: string,
  ) {
    const nonce = `${Date.now()}-${Math.floor(Math.random() * 1_000_000)}`;
    const localPath = this.joinTempPath(`${prefix}-${nonce}.${extension}`);
    await Zotero.File.putContentsAsync(localPath, content);
    const runtime = this.getRuntimeMode();
    const runtimePath =
      runtime === "wsl" ? this.toWslPath(localPath) : localPath;
    return {
      localPath,
      runtimePath,
      windowsPath: localPath,
      wslPath: this.toWslPath(localPath),
    };
  }

  private static async createPromptFiles() {
    return this.writeRuntimeTempFile(
      "paper-flow-prompt",
      "txt",
      String(getPref("promptOverride") || ""),
    );
  }

  private static renderCommand(
    template: string,
    item: Zotero.Item,
    promptFiles: {
      localPath: string;
      runtimePath: string;
      windowsPath: string;
      wslPath: string;
    },
  ) {
    // Item-derived text (title/collection) can contain shell metacharacters.
    // In managed mode these placeholders are unused, but custom templates may
    // reference them unquoted, so strip characters that could break out of the
    // command. Keys and prompt-file paths are plugin-controlled and safe.
    const replacements: Record<string, string> = {
      key: item.key,
      title: this.sanitizeForCommand(this.getItemLabel(item)),
      collection: this.sanitizeForCommand(
        this.getCollectionNames(item)[0] || "",
      ),
      promptFile: promptFiles.runtimePath,
      promptFileLocal: promptFiles.localPath,
      promptFileWindows: promptFiles.windowsPath,
      promptFileWsl: promptFiles.wslPath,
      skipAiFlag: "{{skipAiFlag}}",
    };

    return template.replace(/\{\{(\w+)\}\}/g, (match, token) => {
      return Object.prototype.hasOwnProperty.call(replacements, token)
        ? replacements[token]
        : match;
    });
  }

  /**
   * Produces the final, ready-to-run process command for one item.
   *
   * Custom mode: the user's template is substituted via renderCommand and run
   * as-is. Managed mode: real values are passed straight into the builder, which
   * quotes them and writes the script file — so substitution happens BEFORE the
   * file is written (an earlier refactor wrote the file with raw {{placeholders}}
   * still inside, which broke at runtime).
   */
  private static async buildProcessCommand(
    item: Zotero.Item,
    promptFiles: {
      localPath: string;
      runtimePath: string;
      windowsPath: string;
      wslPath: string;
    },
    syncMode: SyncMode,
  ) {
    if (this.useCustomCommands()) {
      const template = String(getPref("commandTemplate") || "").trim();
      if (!template) {
        return "";
      }
      return this.renderCommand(template, item, promptFiles).replace(
        /\{\{skipAiFlag\}\}/g,
        syncMode === "metadata-only" ? "--skip-ai" : "",
      );
    }
    return this.buildManagedProcessCommand({
      key: item.key,
      title: this.getItemLabel(item),
      collection: this.getCollectionNames(item)[0] || "",
      promptFile: promptFiles.runtimePath,
      promptFileWindows: promptFiles.windowsPath,
      skipAi: syncMode === "metadata-only",
    });
  }

  private static async buildCollectionSyncCommand(
    collection: Zotero.Collection,
    promptFiles: {
      localPath: string;
      runtimePath: string;
      windowsPath: string;
      wslPath: string;
    },
    syncMode: SyncMode,
  ) {
    const runtime = this.getRuntimeMode();
    const workspace = this.resolveWorkspacePath(runtime);
    if (!workspace) {
      return "";
    }
    const collectionName = collection.name || collection.key;
    if (runtime === "native-windows") {
      return [
        this.buildWindowsBootstrap(),
        "&&",
        `cd /d ${this.quoteForCmdArg(workspace)}`,
        "&&",
        'set "AI_BACKEND=command"',
        "&&",
        ...this.buildNotionEnvCmd().flatMap((part) => [part, "&&"]),
        `set "PAPER_FLOW_SYNC_NOTES=${this.escapeForCmdEnvValue(this.getSyncNotesEnvValue())}"`,
        "&&",
        `set "LOCAL_AI_COMMAND=${this.escapeForCmdEnvValue(this.getResolvedAiCommand())}"`,
        "&&",
        `set "LOCAL_AI_ARGS=${this.escapeForCmdEnvValue(this.getResolvedAiArgs())}"`,
        "&&",
        'set "LOCAL_AI_PROMPT_MODE=positional"',
        "&&",
        `set "PAPER_FLOW_GUIDE_LANGUAGE=${this.escapeForCmdEnvValue(this.getGuideLanguage())}"`,
        "&&",
        "uv run paper-notion-flow sync-zotero",
        `--collection ${this.quoteForCmdArg(collectionName)}`,
        "--force",
        syncMode === "metadata-only" ? "--skip-ai" : "",
        `--prompt-override-file ${this.quoteForCmdArg(promptFiles.windowsPath)}`,
      ]
        .filter(Boolean)
        .join(" ");
    }
    const inner = [
      `cd ${this.quoteForBashSingle(workspace)}`,
      "&&",
      `AI_BACKEND=command`,
      ...this.buildNotionEnvBash(),
      `PAPER_FLOW_SYNC_NOTES=${this.quoteForBashSingle(this.getSyncNotesEnvValue())}`,
      `LOCAL_AI_COMMAND=${this.quoteForBashSingle(this.getResolvedAiCommand())}`,
      `LOCAL_AI_ARGS=${this.quoteForBashSingle(this.getResolvedAiArgs())}`,
      "LOCAL_AI_PROMPT_MODE=positional",
      `PAPER_FLOW_GUIDE_LANGUAGE=${this.quoteForBashSingle(this.getGuideLanguage())}`,
      "uv run paper-notion-flow sync-zotero",
      `--collection ${this.quoteForBashSingle(collectionName)}`,
      "--force",
      syncMode === "metadata-only" ? "--skip-ai" : "",
      `--prompt-override-file ${this.quoteForBashSingle(promptFiles.runtimePath)}`,
    ]
      .filter(Boolean)
      .join(" ");
    return this.wrapRuntimeShellCommand(inner, runtime);
  }

  private static async getDeleteCommandTemplate(dryRun: boolean) {
    if (this.useCustomCommands()) {
      return String(getPref("deleteCommandTemplate") || "").trim();
    }
    return this.buildManagedDeleteCommand(dryRun);
  }

  private static getWslDistro() {
    return String(getPref("wslDistro") || "Ubuntu").trim();
  }

  private static getExecutionMode(): ExecutionMode {
    const raw = String(getPref("executionMode") || "auto")
      .trim()
      .toLowerCase();
    if (raw === "native" || raw === "wsl") {
      return raw;
    }
    return "auto";
  }

  private static getRuntimeMode(): RuntimeMode {
    const executionMode = this.getExecutionMode();
    if (executionMode === "wsl" && this.isWindows()) {
      return "wsl";
    }
    if (executionMode === "native") {
      return this.isWindows() ? "native-windows" : "native-unix";
    }
    return this.isWindows() ? "wsl" : "native-unix";
  }

  private static getWorkspacePath() {
    return String(getPref("workspacePath") || "").trim();
  }

  /**
   * Workspace path converted to the ACTIVE runtime's style, so one stored
   * value keeps working when the user switches between WSL and native
   * Windows: /mnt/c/foo <-> C:\foo. Native Unix uses the value as typed.
   */
  private static resolveWorkspacePath(runtime: RuntimeMode): string {
    const raw = this.getWorkspacePath();
    if (!raw) {
      return raw;
    }
    if (runtime === "native-windows") {
      const match = raw.match(/^\/mnt\/([a-z])(\/.*)?$/i);
      if (match) {
        return `${match[1].toUpperCase()}:${(match[2] || "/").replace(/\//g, "\\")}`;
      }
      return raw;
    }
    if (runtime === "wsl" && /^[A-Za-z]:[\\/]/.test(raw)) {
      return this.toWslPath(raw);
    }
    return raw;
  }

  private static getNotionToken() {
    return String(getPref("notionToken") || "").trim();
  }

  private static getNotionDatabaseId() {
    return String(getPref("notionDatabaseId") || "").trim();
  }

  // ---- Config roaming via a Zotero note -----------------------------------
  // Settings that should follow the user across devices (database id, prompt
  // presets, default preset) are stored as JSON inside a clearly-titled
  // standalone note in My Library. Notes are first-class synced Zotero data,
  // so this channel works wherever Zotero account sync works. The Notion
  // token is deliberately NEVER included. Last writer wins via a timestamp.
  private static readonly CONFIG_MARKER = "PAPERFLOW-CONFIG-V1";

  private static async findConfigNote(): Promise<Zotero.Item | null> {
    const search = new Zotero.Search();
    // Typed read-only, but assignable at runtime (standard Zotero pattern).
    (search as any).libraryID = Zotero.Libraries.userLibraryID;
    search.addCondition("itemType", "is", "note");
    search.addCondition("note", "contains", this.CONFIG_MARKER);
    const ids = await search.search();
    if (!ids.length) {
      return null;
    }
    return Zotero.Items.get(ids[0]) as Zotero.Item;
  }

  private static escapeHtml(value: string) {
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  private static unescapeHtml(value: string) {
    return value
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&");
  }

  /** Writes the roaming config into the synced note (creates it if needed). */
  static async pushConfigToZotero() {
    try {
      const payload = {
        databaseId: this.getNotionDatabaseId(),
        promptPresets: this.getPromptPresets(),
        defaultPromptName: this.getDefaultPromptName(),
        updatedAt: Date.now(),
      };
      const html = [
        `<p>${this.CONFIG_MARKER} — Paper Flow settings sync. Do not edit or delete; this note carries your database id and prompt presets to other devices via Zotero sync.</p>`,
        `<pre>${this.escapeHtml(JSON.stringify(payload))}</pre>`,
      ].join("\n");
      let note = await this.findConfigNote();
      if (!note) {
        note = new Zotero.Item("note");
        note.libraryID = Zotero.Libraries.userLibraryID;
      }
      note.setNote(html);
      await note.saveTx();
      setPref("configSyncTs", String(payload.updatedAt));
      ztoolkit.log("Paper Flow pushed roaming config to the sync note.");
    } catch (error) {
      ztoolkit.log(`Paper Flow could not push the config note: ${error}`);
    }
  }

  /**
   * Creates the roaming-config note when this device already has settings but
   * no note exists yet — so a configured device seeds the sync channel
   * automatically at startup instead of waiting for the user to edit
   * something.
   */
  static async ensureConfigSeeded() {
    try {
      if (await this.findConfigNote()) {
        return;
      }
      if (this.getNotionDatabaseId() || this.getPromptPresets().length) {
        await this.pushConfigToZotero();
      }
    } catch (error) {
      ztoolkit.log(`Paper Flow could not seed the config note: ${error}`);
    }
  }

  /**
   * Called from the notifier: when a note carrying our marker arrives via
   * Zotero sync, adopt it immediately — no restart or pane visit needed.
   * Self-pushes are harmless: the pull compares timestamps and no-ops.
   */
  static async adoptIncomingConfigNote(ids: Array<string | number>) {
    try {
      const items = await Zotero.Items.getAsync(ids as number[]);
      const hasConfigNote = items.some(
        (item) =>
          item?.isNote?.() && item.getNote().includes(this.CONFIG_MARKER),
      );
      if (hasConfigNote) {
        await this.pullConfigFromZotero();
      }
    } catch (error) {
      ztoolkit.log(
        `Paper Flow could not adopt an incoming config note: ${error}`,
      );
    }
  }

  /**
   * Adopts config from the synced note when it is newer than what this device
   * last saw. Returns true when anything was adopted.
   */
  static async pullConfigFromZotero(): Promise<boolean> {
    try {
      const note = await this.findConfigNote();
      if (!note) {
        return false;
      }
      const match = note.getNote().match(/<pre>([\s\S]*?)<\/pre>/);
      if (!match) {
        return false;
      }
      const payload = JSON.parse(this.unescapeHtml(match[1]));
      const remoteTs = Number(payload.updatedAt || 0);
      const localTs = Number(getPref("configSyncTs") || "0");
      if (!Number.isFinite(remoteTs) || remoteTs <= localTs) {
        return false;
      }
      if (typeof payload.databaseId === "string" && payload.databaseId) {
        setPref("notionDatabaseId", payload.databaseId);
      }
      if (Array.isArray(payload.promptPresets)) {
        setPref(
          "promptPresets",
          JSON.stringify(
            payload.promptPresets.slice(0, this.MAX_PROMPT_PRESETS),
          ),
        );
      }
      if (typeof payload.defaultPromptName === "string") {
        setPref("defaultPromptName", payload.defaultPromptName);
        this.applyDefaultPrompt();
      }
      setPref("configSyncTs", String(remoteTs));
      this.setStatusMessage(
        "Adopted Paper Flow settings (database id + prompt presets) synced from your Zotero account.",
      );
      return true;
    } catch (error) {
      ztoolkit.log(`Paper Flow could not pull the config note: ${error}`);
      return false;
    }
  }

  /**
   * Env assignments for Notion credentials, omitted when empty: an exported
   * empty NOTION_TOKEN= would mask a valid value in the workspace .env,
   * because load_dotenv() does not override existing environment variables.
   */
  private static buildNotionEnvBash(): string[] {
    const parts: string[] = [];
    const token = this.getNotionToken();
    if (token) {
      parts.push(`NOTION_TOKEN=${this.quoteForBashSingle(token)}`);
    }
    const databaseId = this.getNotionDatabaseId();
    if (databaseId) {
      parts.push(`NOTION_DATABASE_ID=${this.quoteForBashSingle(databaseId)}`);
    }
    return parts;
  }

  private static buildNotionEnvCmd(): string[] {
    const parts: string[] = [];
    const token = this.getNotionToken();
    if (token) {
      parts.push(`set "NOTION_TOKEN=${this.escapeForCmdEnvValue(token)}"`);
    }
    const databaseId = this.getNotionDatabaseId();
    if (databaseId) {
      parts.push(
        `set "NOTION_DATABASE_ID=${this.escapeForCmdEnvValue(databaseId)}"`,
      );
    }
    return parts;
  }

  private static getSyncNotesEnvValue() {
    return getPref("syncNotes") ? "1" : "0";
  }

  private static getAiTool() {
    const aiTool = String(getPref("aiTool") || "codex")
      .trim()
      .toLowerCase();
    return aiTool === "claude" ? "claude" : "codex";
  }

  private static getResolvedAiCommand() {
    return this.getAiTool();
  }

  /**
   * Dropdown options for the model field: stable aliases plus everything the
   * official CLI caches report (refreshed via refreshCliAndModels), so
   * limited-time models like Fable appear with their exact id.
   */
  static getModelOptions(): Array<{ value: string; label: string }> {
    const tool = this.getAiTool();
    const discovered = this.getDiscoveredModels();
    const options: Array<{ value: string; label: string }> = [];
    if (tool === "claude") {
      // Claude aliases always resolve to the latest model of that tier
      // (documented in `claude --help`), so they never need version bumping.
      options.push(
        { value: "", label: "Default (follow Claude Code)" },
        { value: "opus", label: "opus — latest Opus" },
        { value: "sonnet", label: "sonnet — latest Sonnet" },
        { value: "haiku", label: "haiku — latest Haiku" },
      );
      for (const model of discovered.claude) {
        if (!options.some((option) => option.value === model.value)) {
          options.push({
            value: model.value,
            label: `${model.label} (${model.value})`,
          });
        }
      }
      return options;
    }
    const codexDefault = discovered.codex_current
      ? `Default — currently ${discovered.codex_current}`
      : "Default (follow Codex)";
    options.push({ value: "", label: codexDefault });
    for (const model of discovered.codex) {
      if (!options.some((option) => option.value === model.value)) {
        options.push({
          value: model.value,
          label: `${model.label} (${model.value})`,
        });
      }
    }
    return options;
  }

  private static getResolvedAiArgs() {
    const aiTool = String(getPref("aiTool") || "codex")
      .trim()
      .toLowerCase();
    const baseArgs = String(getPref("aiArgs") || "").trim();
    const parts = [baseArgs || this.getDefaultAiArgs(aiTool)];
    const model = String(getPref("aiModel") || "").trim();
    const effort = String(getPref("aiEffort") || "").trim();
    const extraArgs = String(getPref("aiExtraArgs") || "").trim();

    if (
      ["codex", "claude"].includes(aiTool) &&
      model &&
      !this.argsContainOption(parts.join(" "), ["--model", "-m"])
    ) {
      parts.push(`--model ${this.quoteForShlexArg(model)}`);
    }

    if (
      ["codex", "claude"].includes(aiTool) &&
      effort &&
      !this.argsContainEffort(parts.join(" "))
    ) {
      if (aiTool === "claude") {
        parts.push(`--effort ${this.quoteForShlexArg(effort)}`);
      } else if (aiTool === "codex") {
        parts.push(
          `-c ${this.quoteForShlexArg(`model_reasoning_effort="${effort}"`)}`,
        );
      }
    }

    if (extraArgs) {
      parts.push(extraArgs);
    }

    return parts.filter(Boolean).join(" ");
  }

  private static getDefaultAiArgs(aiTool: string) {
    if (aiTool === "claude") {
      return "--print";
    }
    return "exec --skip-git-repo-check";
  }

  // ---- CLI auto-update ----------------------------------------------------

  private static async buildUpdateCommand(tool: string, runtime: RuntimeMode) {
    const claudeInner =
      "claude update || npm install -g @anthropic-ai/claude-code@latest; claude --version";
    const codexInner = "npm install -g @openai/codex@latest && codex --version";
    if (runtime === "native-windows") {
      const inner =
        tool === "claude"
          ? "claude update & claude --version"
          : "npm install -g @openai/codex@latest & codex --version";
      return [this.buildWindowsBootstrap(), inner].join(" & ");
    }
    return this.wrapRuntimeShellCommand(
      tool === "claude" ? claudeInner : codexInner,
      runtime,
    );
  }

  /** Updates the selected CLI to the latest version and reports the result. */
  static async updateAiCli(reason: "manual" | "auto") {
    const tool = this.getAiTool();
    const runtime = this.getRuntimeMode();
    const command = await this.buildUpdateCommand(tool, runtime);
    this.setStatusMessage(`Updating ${tool} CLI (${reason})...`);
    if (this.shouldShowNotifications()) {
      this.showProgress(`Updating ${tool} CLI...`, "default");
    }
    try {
      const output = await this.executeShellCommandWithOutput(command);
      const message = await this.buildDiagnosticMessage({
        title: `${tool} CLI update finished.`,
        command,
        output: output.trim() || "(no output)",
      });
      this.setStatusMessage(message);
      if (this.shouldShowNotifications()) {
        this.showProgress(`${tool} CLI is up to date.`, "success");
      }
    } catch (error) {
      const details = error instanceof Error ? error.message : String(error);
      const message = await this.buildDiagnosticMessage({
        title: `${tool} CLI update failed.`,
        command,
        output: details,
      });
      this.setStatusMessage(message);
      this.copyTextToClipboard(message);
      if (this.shouldShowNotifications()) {
        this.showProgress(`${tool} CLI update failed.`, "error");
      }
    }
  }

  /**
   * Runs a throttled background update at startup when the toggle is on.
   * The throttle is PER TOOL: updating claude yesterday must not suppress a
   * needed codex update today after the user switches tools.
   */
  static autoUpdateOnStartup() {
    if (!this.isEnabled() || !getPref("autoUpdateCli")) {
      return;
    }
    const tool = this.getAiTool();
    const now = Date.now();
    let lastByTool: Record<string, number> = {};
    try {
      const parsed = JSON.parse(String(getPref("lastUpdateCheck") || "{}"));
      if (parsed && typeof parsed === "object") {
        lastByTool = parsed;
      }
    } catch (_error) {
      // Legacy value was a plain timestamp string; treat as never-checked.
    }
    const last = Number(lastByTool[tool] || 0);
    if (Number.isFinite(last) && now - last < 24 * 60 * 60 * 1000) {
      return;
    }
    lastByTool[tool] = now;
    setPref("lastUpdateCheck", JSON.stringify(lastByTool));
    void this.enqueueRun(() => this.updateAiCli("auto"));
  }

  /**
   * Reads the OFFICIAL model lists both CLIs keep on disk, plus the installed
   * and latest CLI versions. No guessing and no paid calls:
   * - claude: ~/.claude.json additionalModelOptionsCache — the exact data the
   *   interactive /model menu shows (e.g. limited-time models like Fable).
   * - codex: ~/.codex/models_cache.json (slug/display_name/efforts, official
   *   server-synced cache) and config.toml's current model.
   * Results are cached in the modelOptionsCache pref and feed the dropdown.
   */
  static async refreshCliAndModels(): Promise<{
    tool: string;
    current: string;
    latest: string;
  } | null> {
    const runtime = this.getRuntimeMode();
    const workspace = this.resolveWorkspacePath(runtime);
    if (!workspace) {
      this.setStatusMessage(
        "Set the workspace path first; model discovery runs through `uv run python` in the workspace.",
      );
      return null;
    }
    const tool = this.getAiTool();
    const script = this.buildModelDiscoveryScript(tool);
    const files = await this.writeRuntimeTempFile(
      "paper-flow-model-discovery",
      "py",
      script,
    );
    const command =
      runtime === "native-windows"
        ? [
            this.buildWindowsBootstrap(),
            `cd /d ${this.quoteForCmdArg(workspace)}`,
            `uv run python ${this.quoteForCmdArg(files.windowsPath)}`,
          ].join(" & ")
        : await this.wrapRuntimeShellCommand(
            `cd ${this.quoteForBashSingle(workspace)} && uv run python ${this.quoteForBashSingle(files.runtimePath)}`,
            runtime,
          );
    try {
      const output = await this.executeShellCommandWithOutput(command);
      const line = output
        .split(/\r?\n/)
        .find((entry) => entry.startsWith("MODEL_OPTIONS_JSON:"));
      if (!line) {
        this.setStatusMessage(
          `Model discovery produced no data.\n\n${output.trim()}`,
        );
        return null;
      }
      const payload = JSON.parse(line.slice("MODEL_OPTIONS_JSON:".length));
      setPref("modelOptionsCache", JSON.stringify(payload));
      const cli = payload.cli || {};
      const claudeCount = (payload.claude || []).length;
      const codexCount = (payload.codex || []).length;
      this.setStatusMessage(
        [
          `CLI:${cli.tool || tool}`,
          `CLI_CURRENT:${cli.current || "UNKNOWN"}`,
          `CLI_LATEST:${cli.latest || "UNKNOWN"}`,
          `CLAUDE_EXTRA_MODELS:${claudeCount}`,
          `CODEX_MODELS:${codexCount}`,
          payload.codex_current
            ? `CODEX_CURRENT_DEFAULT:${payload.codex_current}`
            : "",
          "Model dropdown refreshed from the official CLI caches.",
        ]
          .filter(Boolean)
          .join("\n"),
      );
      return {
        tool,
        current: String(cli.current || ""),
        latest: String(cli.latest || ""),
      };
    } catch (error) {
      const details = error instanceof Error ? error.message : String(error);
      this.setStatusMessage(`Model discovery failed: ${details}`);
      return null;
    }
  }

  private static buildModelDiscoveryScript(tool: string) {
    return [
      "import json, re, shutil, subprocess, sys",
      "from pathlib import Path",
      "home = Path.home()",
      'out = {"claude": [], "codex": [], "codex_current": None, "cli": {}}',
      "try:",
      '    data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))',
      '    for opt in (data.get("additionalModelOptionsCache") or []):',
      '        value = opt.get("value")',
      "        if value:",
      '            out["claude"].append({"value": value, "label": opt.get("label") or value, "description": opt.get("description") or ""})',
      "except Exception:",
      "    pass",
      "try:",
      '    data = json.loads((home / ".codex" / "models_cache.json").read_text(encoding="utf-8"))',
      '    for model in (data.get("models") or []):',
      '        if model.get("visibility") not in (None, "list"):',
      "            continue",
      '        slug = model.get("slug")',
      "        if slug:",
      '            out["codex"].append({"value": slug, "label": model.get("display_name") or slug, "description": model.get("description") or "", "context": model.get("context_window"), "efforts": [level.get("effort") for level in (model.get("supported_reasoning_levels") or []) if level.get("effort")]})',
      "except Exception:",
      "    pass",
      "try:",
      '    config = (home / ".codex" / "config.toml").read_text(encoding="utf-8")',
      '    match = re.search(r\'^model\\s*=\\s*"([^"]+)"\', config, re.M)',
      "    if match:",
      '        out["codex_current"] = match.group(1)',
      "except Exception:",
      "    pass",
      "def _run(command):",
      "    try:",
      "        return subprocess.run(command, capture_output=True, text=True, timeout=60, check=False).stdout.strip()",
      "    except Exception:",
      '        return ""',
      `tool = ${JSON.stringify(tool)}`,
      'package = "@anthropic-ai/claude-code" if tool == "claude" else "@openai/codex"',
      "current = _run([tool, '--version']).splitlines()[0] if shutil.which(tool) else ''",
      "latest = _run(['npm', 'view', package, 'version', '--silent']) if shutil.which('npm') else ''",
      'out["cli"] = {"tool": tool, "current": current, "latest": latest}',
      'print("MODEL_OPTIONS_JSON:" + json.dumps(out, ensure_ascii=False))',
    ].join("\n");
  }

  /** Parsed modelOptionsCache pref. */
  static getDiscoveredModels(): {
    claude: Array<{ value: string; label: string; description?: string }>;
    codex: Array<{
      value: string;
      label: string;
      description?: string;
      context?: number;
      efforts?: string[];
    }>;
    codex_current?: string | null;
  } {
    const empty = { claude: [], codex: [], codex_current: null };
    const raw = String(getPref("modelOptionsCache") || "").trim();
    if (!raw) {
      return empty;
    }
    try {
      const parsed = JSON.parse(raw);
      return {
        claude: Array.isArray(parsed.claude) ? parsed.claude : [],
        codex: Array.isArray(parsed.codex) ? parsed.codex : [],
        codex_current: parsed.codex_current || null,
      };
    } catch (_error) {
      return empty;
    }
  }

  /**
   * Effort levels for the effort dropdown. For codex, levels come from the
   * discovered models cache (per-model supported_reasoning_levels) when the
   * selected model is known; otherwise sane defaults per CLI.
   */
  static getEffortOptions(): string[] {
    const tool = this.getAiTool();
    if (tool === "claude") {
      return ["low", "medium", "high", "xhigh", "max"];
    }
    const model = String(getPref("aiModel") || "").trim();
    const discovered = this.getDiscoveredModels().codex as Array<{
      value: string;
      efforts?: string[];
    }>;
    const entry = model
      ? discovered.find((item) => item.value === model)
      : null;
    if (entry?.efforts?.length) {
      return entry.efforts;
    }
    return ["low", "medium", "high", "xhigh"];
  }

  private static argsContainOption(args: string, options: string[]) {
    return options.some((option) =>
      new RegExp(`(^|\\s)${this.escapeRegExp(option)}(\\s|=|$)`).test(args),
    );
  }

  private static argsContainEffort(args: string) {
    return (
      this.argsContainOption(args, ["--effort"]) ||
      args.includes("model_reasoning_effort")
    );
  }

  private static quoteForShlexArg(value: string) {
    if (/^[A-Za-z0-9_./:=@+-]+$/.test(value)) {
      return value;
    }
    return this.quoteForBashSingle(value);
  }

  private static escapeRegExp(value: string) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /**
   * Composes the managed command for the given values (real values at run time,
   * or {{placeholders}} for the preview). Returns either a `full` command
   * (native Windows, run directly) or an `inner` shell command (wsl/native-unix,
   * to be wrapped into a script file). Does NOT write any file, so it is safe to
   * call synchronously for previews.
   */
  private static composeManagedProcessInner(values: {
    key: string;
    title: string;
    collection: string;
    promptFile: string;
    promptFileWindows: string;
    skipAi: boolean;
  }): { runtime: RuntimeMode; inner?: string; full?: string } | null {
    const runtime = this.getRuntimeMode();
    const workspace = this.resolveWorkspacePath(runtime);
    if (!workspace) {
      return null;
    }
    if (runtime === "native-windows") {
      return {
        runtime,
        full: this.buildNativeWindowsProcessCommand(workspace, values),
      };
    }
    const inner = [
      `cd ${this.quoteForBashSingle(workspace)}`,
      "&&",
      `AI_BACKEND=command`,
      ...this.buildNotionEnvBash(),
      `PAPER_FLOW_SYNC_NOTES=${this.quoteForBashSingle(this.getSyncNotesEnvValue())}`,
      `LOCAL_AI_COMMAND=${this.quoteForBashSingle(this.getResolvedAiCommand())}`,
      `LOCAL_AI_ARGS=${this.quoteForBashSingle(this.getResolvedAiArgs())}`,
      "LOCAL_AI_PROMPT_MODE=positional",
      `PAPER_FLOW_GUIDE_LANGUAGE=${this.quoteForBashSingle(this.getGuideLanguage())}`,
      "uv run paper-notion-flow process-zotero-item",
      `--key ${this.quoteForBashSingle(values.key)}`,
      "--force",
      values.skipAi ? "--skip-ai" : "",
      `--prompt-override-file ${this.quoteForBashSingle(values.promptFile)}`,
    ]
      .filter(Boolean)
      .join(" ");
    return { runtime, inner };
  }

  private static async buildManagedProcessCommand(values: {
    key: string;
    title: string;
    collection: string;
    promptFile: string;
    promptFileWindows: string;
    skipAi: boolean;
  }) {
    const composed = this.composeManagedProcessInner(values);
    if (!composed) {
      return "";
    }
    if (composed.full !== undefined) {
      return composed.full;
    }
    return this.wrapRuntimeShellCommand(composed.inner ?? "", composed.runtime);
  }

  private static async buildManagedDeleteCommand(dryRun: boolean) {
    const runtime = this.getRuntimeMode();
    const workspace = this.resolveWorkspacePath(runtime);
    if (!workspace) {
      return "";
    }
    if (runtime === "native-windows") {
      return [
        this.buildWindowsBootstrap(),
        "&&",
        `cd /d ${this.quoteForCmdArg(workspace)}`,
        "&&",
        "uv run paper-notion-flow prune-zotero-deletions",
        dryRun ? "--dry-run" : "",
      ]
        .filter(Boolean)
        .join(" ");
    }
    return this.wrapRuntimeShellCommand(
      `cd ${this.quoteForBashSingle(workspace)} && uv run paper-notion-flow prune-zotero-deletions${dryRun ? " --dry-run" : ""}`,
      runtime,
    );
  }

  private static getItemLabel(item: Zotero.Item) {
    return item.getDisplayTitle?.() || item.getField("title") || item.key;
  }

  /**
   * Wraps an inner shell command for the target runtime.
   *
   * For WSL and native Unix the bootstrap + command are written to a real `.sh`
   * file and executed with `bash -l <file>`, instead of being escaped into
   * `bash -lc "<...>"`. The inline form was routed through cmd.exe -> wsl.exe ->
   * bash, where the nested quoting could double-evaluate the script: a Windows
   * PATH inherited into WSL (entries like "Program Files (x86)") got expanded
   * once and then re-parsed as code, breaking bash on the `(`. A script file
   * removes that whole quoting/eval layer.
   */
  private static async wrapRuntimeShellCommand(
    inner: string,
    runtime: RuntimeMode,
  ) {
    if (runtime === "native-windows") {
      return [this.buildWindowsBootstrap(), inner].join(" & ");
    }
    const script = `${[this.buildUnixBootstrap(runtime === "wsl"), inner].join("\n")}\n`;
    const files = await this.writeRuntimeTempFile(
      "paper-flow-run",
      "sh",
      script,
    );
    if (runtime === "wsl") {
      const distro = this.getWslDistro();
      const distroArgs = distro ? `-d ${this.quoteForCmdToken(distro)}` : "";
      // The script path is parsed by wsl.exe's Windows argv splitter and execed
      // directly (no shell), so it needs cmd-style double quoting, not bash
      // quoting — otherwise the quotes become part of the filename.
      return `wsl.exe ${distroArgs} bash -l ${this.quoteForCmdArg(files.wslPath)}`;
    }
    // native-unix runs via /bin/sh -lc, so bash-quote the path here.
    return `bash -l ${this.quoteForBashSingle(files.localPath)}`;
  }

  private static async buildSetupCheckCommand(options: {
    modeLabel: string;
    runtime: RuntimeMode;
    selectedAi: string;
    workspacePath: string;
    pythonCheck: {
      localPath: string;
      runtimePath: string;
      windowsPath: string;
      wslPath: string;
    };
  }) {
    // Echo the exact model configuration so the check output answers
    // "which model and effort will actually run" at a glance.
    const selectedModel = String(getPref("aiModel") || "").trim() || "default";
    const selectedEffort =
      String(getPref("aiEffort") || "").trim() || "default";
    if (!options.workspacePath) {
      const lines = [
        `echo MODE:${options.modeLabel}`,
        `echo RUNTIME:${options.runtime}`,
        `echo PLATFORM:${this.getPlatformLabel()}`,
        `echo GUIDE_LANGUAGE:${this.getGuideLanguage()}`,
        `echo NOTION_TOKEN_PREF:${this.getNotionToken() ? "OK" : "MISSING"}`,
        `echo NOTION_DATABASE_ID_PREF:${this.getNotionDatabaseId() ? "OK" : "MISSING"}`,
        `echo SELECTED_AI:${options.selectedAi}`,
        "echo WORKSPACE_PATH:MISSING",
        "echo WORKSPACE:MISSING",
        "echo FIX: Set the Paper Flow workspace path to the folder containing pyproject.toml.",
      ];
      if (options.runtime === "native-windows") {
        return [...lines, "exit /b 1"].join(" & ");
      }
      return this.wrapRuntimeShellCommand(
        [...lines, "exit 1"].join(" ; "),
        options.runtime,
      );
    }

    if (options.runtime === "native-windows") {
      return [
        this.buildWindowsBootstrap(),
        `echo MODE:${options.modeLabel}`,
        `echo RUNTIME:${options.runtime}`,
        `echo PLATFORM:${this.getPlatformLabel()}`,
        `echo GUIDE_LANGUAGE:${this.getGuideLanguage()}`,
        `echo NOTION_TOKEN_PREF:${this.getNotionToken() ? "OK" : "MISSING"}`,
        `echo NOTION_DATABASE_ID_PREF:${this.getNotionDatabaseId() ? "OK" : "MISSING"}`,
        `echo SELECTED_AI:${options.selectedAi}`,
        `echo SELECTED_MODEL:${selectedModel}`,
        `echo SELECTED_EFFORT:${selectedEffort}`,
        `echo WORKSPACE_PATH:${options.workspacePath}`,
        `if exist ${this.quoteForCmdArg(options.workspacePath)} (echo WORKSPACE:OK) else (echo WORKSPACE:MISSING & exit /b 1)`,
        `cd /d ${this.quoteForCmdArg(options.workspacePath)}`,
        this.buildWindowsCommandCheck("UV", "uv"),
        this.buildWindowsCommandCheck("CODEX", "codex"),
        this.buildWindowsCommandCheck("CLAUDE", "claude"),
        this.buildWindowsCommandCheck("SELECTED_AI", options.selectedAi),
        "uv run paper-notion-flow --help >nul 2>nul && echo PAPER_NOTION_FLOW:OK || echo PAPER_NOTION_FLOW:MISSING",
        ...this.buildNotionEnvCmd(),
        `set "PAPER_FLOW_SYNC_NOTES=${this.escapeForCmdEnvValue(this.getSyncNotesEnvValue())}"`,
        `uv run python ${this.quoteForCmdArg(options.pythonCheck.windowsPath)}`,
      ].join(" & ");
    }

    const shellChecks = [
      `echo MODE:${options.modeLabel}`,
      `echo RUNTIME:${options.runtime}`,
      `echo PLATFORM:${this.getPlatformLabel()}`,
      `echo GUIDE_LANGUAGE:${this.getGuideLanguage()}`,
      `echo NOTION_TOKEN_PREF:${this.getNotionToken() ? "OK" : "MISSING"}`,
      `echo NOTION_DATABASE_ID_PREF:${this.getNotionDatabaseId() ? "OK" : "MISSING"}`,
      `echo SELECTED_AI:${options.selectedAi}`,
      // Single-quoted: model ids like claude-fable-5[1m] contain glob chars.
      `echo ${this.quoteForBashSingle(`SELECTED_MODEL:${selectedModel}`)}`,
      `echo ${this.quoteForBashSingle(`SELECTED_EFFORT:${selectedEffort}`)}`,
      `echo WSL_DISTRO:${options.runtime === "wsl" ? this.getWslDistro() || "default" : "not-applicable"}`,
      `echo WORKSPACE_PATH:${options.workspacePath}`,
      `if [ -d ${this.quoteForBashSingle(options.workspacePath)} ]; then echo WORKSPACE:OK; else echo WORKSPACE:MISSING; exit 1; fi`,
      `cd ${this.quoteForBashSingle(options.workspacePath)}`,
      "if command -v uv >/dev/null 2>&1; then echo UV:OK; else echo UV:MISSING; fi",
      "if command -v codex >/dev/null 2>&1; then echo CODEX:OK; else echo CODEX:MISSING; fi",
      "if command -v claude >/dev/null 2>&1; then echo CLAUDE:OK; else echo CLAUDE:MISSING; fi",
      `if command -v ${this.quoteForBashSingle(options.selectedAi)} >/dev/null 2>&1; then echo SELECTED_AI:OK; else echo SELECTED_AI:MISSING; fi`,
      // In WSL mode the AI binary MUST live inside the distro. A /mnt/* hit
      // means Windows interop leaked in — fail loudly instead of running it.
      options.runtime === "wsl"
        ? `AI_BIN=$(command -v ${this.quoteForBashSingle(options.selectedAi)} 2>/dev/null); case "$AI_BIN" in /mnt/*) echo AI_PATH_SCOPE:WINDOWS_LEAK; echo "FIX: install ${options.selectedAi} inside WSL (it currently resolves to $AI_BIN)"; exit 1;; "") :;; *) echo AI_PATH_SCOPE:NATIVE;; esac`
        : "echo AI_PATH_SCOPE:not-applicable",
      "if uv run paper-notion-flow --help >/dev/null 2>&1; then echo PAPER_NOTION_FLOW:OK; else echo PAPER_NOTION_FLOW:MISSING; fi",
      [
        ...this.buildNotionEnvBash(),
        `PAPER_FLOW_SYNC_NOTES=${this.quoteForBashSingle(this.getSyncNotesEnvValue())}`,
        `uv run python ${this.quoteForBashSingle(options.pythonCheck.runtimePath)}`,
      ].join(" "),
    ].join(" ; ");
    return this.wrapRuntimeShellCommand(shellChecks, options.runtime);
  }

  private static buildNativeWindowsProcessCommand(
    workspace: string,
    placeholders: {
      key: string;
      title: string;
      collection: string;
      promptFile: string;
      promptFileWindows: string;
      skipAi: boolean;
    },
  ) {
    return [
      this.buildWindowsBootstrap(),
      "&&",
      `cd /d ${this.quoteForCmdArg(workspace)}`,
      "&&",
      'set "AI_BACKEND=command"',
      "&&",
      ...this.buildNotionEnvCmd().flatMap((part) => [part, "&&"]),
      `set "PAPER_FLOW_SYNC_NOTES=${this.escapeForCmdEnvValue(this.getSyncNotesEnvValue())}"`,
      "&&",
      `set "LOCAL_AI_COMMAND=${this.escapeForCmdEnvValue(this.getResolvedAiCommand())}"`,
      "&&",
      `set "LOCAL_AI_ARGS=${this.escapeForCmdEnvValue(this.getResolvedAiArgs())}"`,
      "&&",
      'set "LOCAL_AI_PROMPT_MODE=positional"',
      "&&",
      `set "PAPER_FLOW_GUIDE_LANGUAGE=${this.escapeForCmdEnvValue(this.getGuideLanguage())}"`,
      "&&",
      "uv run paper-notion-flow process-zotero-item",
      `--key ${this.quoteForCmdArg(placeholders.key)}`,
      "--force",
      placeholders.skipAi ? "--skip-ai" : "",
      `--prompt-override-file ${this.quoteForCmdArg(placeholders.promptFile)}`,
    ]
      .filter(Boolean)
      .join(" ");
  }

  private static buildUnixBootstrap(isWsl = false) {
    // Under WSL, Windows tools (npm, pnpm) leak in via interop and return
    // Windows-style paths (e.g. C:\Users\...\npm). The `case /*` guards keep
    // those out of PATH so only real POSIX paths are added.
    const lines = [
      'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.volta/bin:$HOME/.asdf/shims:$HOME/.local/share/mise/shims:$HOME/.bun/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"',
      "if [ -s $HOME/.nvm/nvm.sh ]; then . $HOME/.nvm/nvm.sh; fi",
      "if command -v nvm >/dev/null 2>&1; then nvm use --silent default >/dev/null 2>&1 || true; fi",
      'if command -v fnm >/dev/null 2>&1; then eval "$(fnm env --shell bash)" >/dev/null 2>&1 || true; fi',
      'if command -v npm >/dev/null 2>&1; then NPM_PREFIX=$(npm prefix -g 2>/dev/null || true); case "$NPM_PREFIX" in /*) export PATH="$NPM_PREFIX/bin:$NPM_PREFIX:$PATH";; esac; fi',
      'if command -v pnpm >/dev/null 2>&1; then PNPM_BIN=$(pnpm bin -g 2>/dev/null || true); case "$PNPM_BIN" in /*) export PATH="$PNPM_BIN:$PATH";; esac; fi',
    ];
    if (isWsl) {
      // In WSL mode tools MUST resolve inside the distro. Windows interop
      // appends /mnt/c/... entries to PATH, and a Windows-installed codex or
      // node there either fails ("node: not found") or silently runs the wrong
      // binary. Strip every /mnt/* entry so resolution can never leave WSL.
      lines.push(
        `PATH=$(printf '%s' "$PATH" | tr ':' '\\n' | grep -v '^/mnt/' | tr '\\n' ':' | sed 's/:*$//') ; export PATH`,
      );
    }
    lines.push("hash -r 2>/dev/null || true");
    return lines.join(" ; ");
  }

  private static buildWindowsBootstrap() {
    return [
      'set "PATH=%USERPROFILE%\\.local\\bin;%USERPROFILE%\\.cargo\\bin;%APPDATA%\\npm;%LOCALAPPDATA%\\Volta\\bin;%LOCALAPPDATA%\\fnm_multishells;%NVM_SYMLINK%;%NVM_HOME%;%ProgramFiles%\\nodejs;%ProgramFiles(x86)%\\nodejs;%PATH%"',
      'for /f "delims=" %%I in (\'npm prefix -g 2^>nul\') do if exist "%%I" set "PATH=%%I;%%I\\bin;%PATH%"',
    ].join(" & ");
  }

  private static buildWindowsCommandCheck(label: string, command: string) {
    const trimmed = command.trim();
    if (!trimmed) {
      return `echo ${label}:MISSING`;
    }
    if (/^[A-Za-z0-9_.-]+$/.test(trimmed)) {
      return `where ${trimmed} >nul 2>nul && echo ${label}:OK || echo ${label}:MISSING`;
    }
    return `if exist ${this.quoteForCmdArg(trimmed)} (echo ${label}:OK) else (where ${this.quoteForCmdArg(trimmed)} >nul 2>nul && echo ${label}:OK || echo ${label}:MISSING)`;
  }

  private static getGuideLanguage() {
    const raw = String(getPref("guideLanguage") || "zh-CN").trim();
    if (Object.prototype.hasOwnProperty.call(LANGUAGE_NAMES, raw)) {
      return raw as GuideLanguage;
    }
    return "zh-CN";
  }

  private static buildPythonRuntimeCheck(selectedAi: string) {
    const versionCheckScript = [
      "import re, shutil, subprocess, sys",
      "def _version_number(text):",
      "    match=re.search(r'\\d+(?:\\.\\d+){1,3}', text or '')",
      "    return match.group(0) if match else ''",
      "def _first_line(text):",
      "    lines=(text or '').strip().splitlines()",
      "    return lines[0].strip() if lines else 'UNKNOWN'",
      "def _run_text(command, timeout=8, env=None):",
      "    try:",
      "        completed=subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout, env=env)",
      "    except Exception as error:",
      "        return '', str(error), 1",
      "    return completed.stdout, completed.stderr, completed.returncode",
      "def _help_text(command, args):",
      "    resolved=_resolve_local_command(command)",
      "    if resolved == command and not shutil.which(command):",
      "        return ''",
      "    stdout, stderr, code=_run_text([resolved,*args], env=_build_local_ai_env(resolved))",
      "    return stdout or stderr",
      "def _option_line(help_text, option):",
      "    for line in (help_text or '').splitlines():",
      "        if option in line:",
      "            return ' '.join(line.split())",
      "    return ''",
      "def _print_capability(label, key, value):",
      "    print(f'{label}_{key}:'+str(value or 'UNKNOWN'))",
      "def _print_codex_capabilities():",
      "    top_help=_help_text('codex',['--help'])",
      "    exec_help=_help_text('codex',['exec','--help'])",
      "    _print_capability('CODEX','NON_INTERACTIVE_ENTRY', 'codex exec [OPTIONS] [PROMPT]')",
      "    _print_capability('CODEX','SKIP_GIT_REPO_CHECK_HELP', _option_line(exec_help,'--skip-git-repo-check'))",
      "    _print_capability('CODEX','MODEL_FLAG', '--model <MODEL>' if '--model <MODEL>' in exec_help or '--model <MODEL>' in top_help else 'UNKNOWN')",
      "    _print_capability('CODEX','MODEL_HELP', _option_line(exec_help,'--model') or _option_line(top_help,'--model'))",
      "    _print_capability('CODEX','MODEL_DISCOVERY', 'UNAVAILABLE_IN_CURRENT_CLI_HELP')",
      "    _print_capability('CODEX','MODEL_HINTS', 'free-form model id; availability depends on Codex version, account, and provider')",
      "    _print_capability('CODEX','EFFORT_FLAG', '-c model_reasoning_effort=\"<level>\"')",
      "    _print_capability('CODEX','EFFORT_DISCOVERY', 'NOT_EXPOSED_BY_CURRENT_CLI_HELP')",
      "    _print_capability('CODEX','EFFORT_LEVELS_HINT', 'provider/model dependent; common values: low,medium,high,xhigh')",
      "def _print_claude_capabilities():",
      "    help_text=_help_text('claude',['--help'])",
      "    _print_capability('CLAUDE','NON_INTERACTIVE_ENTRY', 'claude --print [OPTIONS] [PROMPT]')",
      "    _print_capability('CLAUDE','EXEC_COMMAND', 'NOT_EXPOSED_BY_CURRENT_CLI_HELP')",
      "    _print_capability('CLAUDE','MODEL_FLAG', '--model <model>' if '--model <model>' in help_text else 'UNKNOWN')",
      "    _print_capability('CLAUDE','MODEL_HELP', _option_line(help_text,'--model'))",
      "    _print_capability('CLAUDE','MODEL_DISCOVERY', 'UNAVAILABLE_IN_CURRENT_CLI_HELP')",
      "    _print_capability('CLAUDE','MODEL_HINTS', 'aliases such as sonnet or opus; full names such as claude-sonnet-4-6 when supported')",
      "    _print_capability('CLAUDE','EFFORT_FLAG', '--effort <level>' if '--effort <level>' in help_text else 'UNKNOWN')",
      "    _print_capability('CLAUDE','EFFORT_HELP', _option_line(help_text,'--effort'))",
      "def _latest_npm_version(package):",
      "    npm=_resolve_local_command('npm')",
      "    if npm == 'npm' and not shutil.which('npm'):",
      "        return ''",
      "    stdout, stderr, code=_run_text([npm,'view',package,'version','--silent'], env=_build_local_ai_env(npm))",
      "    if code != 0:",
      "        return ''",
      "    return _first_line(stdout)",
      "def _print_command_version(label, command):",
      "    resolved=_resolve_local_command(command)",
      "    if resolved == command and not shutil.which(command):",
      "        print(f'{label}_VERSION:MISSING')",
      "        return ''",
      "    print(f'{label}_PATH:'+resolved)",
      "    stdout, stderr, code=_run_text([resolved,'--version'], env=_build_local_ai_env(resolved))",
      "    text=_first_line(stdout or stderr)",
      "    print(f'{label}_VERSION:'+text)",
      "    return _version_number(text)",
      "def _print_ai_update(label, command, npm_package, update_command):",
      "    current=_print_command_version(label, command)",
      "    if not current:",
      "        print(f'{label}_LATEST:UNKNOWN')",
      "        print(f'{label}_UPDATE:UNKNOWN')",
      "        return",
      "    latest=_latest_npm_version(npm_package)",
      "    print(f'{label}_LATEST:'+(latest or 'UNKNOWN'))",
      "    if not latest:",
      "        print(f'{label}_UPDATE:UNKNOWN')",
      "    elif latest == current:",
      "        print(f'{label}_UPDATE:OK')",
      "    else:",
      "        print(f'{label}_UPDATE:AVAILABLE')",
      "        print(f'{label}_UPDATE_COMMAND:'+update_command)",
      "print('PYTHON_VERSION:'+sys.version.split()[0])",
      "_print_command_version('UV','uv')",
      "_print_ai_update('CODEX','codex','@openai/codex','npm install -g @openai/codex@latest')",
      "_print_ai_update('CLAUDE','claude','@anthropic-ai/claude-code','claude update')",
      "_print_codex_capabilities()",
      "_print_claude_capabilities()",
    ].join("\n");
    return [
      "from paper_notion_flow.ai import _build_local_ai_env, _resolve_local_command",
      "from paper_notion_flow.config import Settings",
      "from paper_notion_flow.workflow import check_notion",
      "settings=Settings()",
      "missing=[]",
      "print('PY_IMPORT:OK')",
      `print('RESOLVED_AI_COMMAND:'+_resolve_local_command(${JSON.stringify(selectedAi)}))`,
      "print('NOTION_TOKEN:'+('OK' if settings.notion_token else 'MISSING'))",
      "print('NOTION_DATABASE_ID:'+('OK' if settings.notion_database_id else 'MISSING'))",
      "print('SYNC_NOTES:'+('ON' if settings.sync_notes else 'OFF'))",
      "print(check_notion(settings) if settings.notion_token and settings.notion_database_id else 'NOTION_SCHEMA:SKIPPED_MISSING_CONFIG')",
      "print('GUIDE_LANGUAGE:'+settings.guide_language)",
      `exec(${JSON.stringify(versionCheckScript)})`,
      "print('ZOTERO_DATA_DIR:'+str(settings.zotero_data_dir))",
      "print('ZOTERO_DB:'+('OK' if (settings.zotero_data_dir / 'zotero.sqlite').exists() else 'MISSING'))",
      "missing.extend(name for name, ok in [('NOTION_TOKEN', bool(settings.notion_token)), ('NOTION_DATABASE_ID', bool(settings.notion_database_id)), ('ZOTERO_DB', (settings.zotero_data_dir / 'zotero.sqlite').exists())] if not ok)",
      "raise SystemExit(1 if missing else 0)",
    ].join("; ");
  }

  private static quoteForCmdToken(value: string) {
    if (/^[A-Za-z0-9_.-]+$/.test(value)) {
      return value;
    }
    return `"${value.replace(/"/g, '""')}"`;
  }

  private static quoteForBashSingle(value: string) {
    const escaped = value.replace(/'/g, "'\"'\"'");
    return `'${escaped}'`;
  }

  private static quoteForCmdArg(value: string) {
    return `"${value.replace(/"/g, '""')}"`;
  }

  private static escapeForCmdEnvValue(value: string) {
    // Inside `set "VAR=value"` the quotes make &, ^, | literal, but % still
    // triggers environment-variable expansion in a batch file, so double it.
    return value.replace(/%/g, "%%").replace(/"/g, '""');
  }

  /**
   * Removes shell metacharacters from item-derived text so it cannot break out
   * of a command, even when a custom template inserts it unquoted.
   */
  private static sanitizeForCommand(value: string) {
    return value
      .replace(/[`$;&|<>(){}"'\\\r\n]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  private static toWslPath(path: string) {
    const normalized = path.replace(/\\/g, "/");
    const match = normalized.match(/^([A-Za-z]):\/(.*)$/);
    if (!match) {
      return normalized;
    }
    return `/mnt/${match[1].toLowerCase()}/${match[2]}`;
  }

  private static isWindows() {
    return Boolean((Zotero as any).isWin);
  }

  private static getPlatformLabel() {
    if (this.isWindows()) {
      return "windows";
    }
    if ((Zotero as any).isMac) {
      return "macos";
    }
    return "linux";
  }

  private static joinTempPath(fileName: string) {
    const separator = this.isWindows() ? "\\" : "/";
    return `${Zotero.getTempDirectory().path}${separator}${fileName}`;
  }

  private static async executeShellCommand(command: string) {
    let shellPath = "/bin/sh";
    let args = ["-lc", command];

    if (this.isWindows()) {
      shellPath = "C:\\Windows\\System32\\cmd.exe";
      const scriptPath = `${Zotero.getTempDirectory().path}\\paper-flow-run-${Date.now()}-${Math.floor(Math.random() * 1_000_000)}.cmd`;
      const script = [
        "@echo off",
        "chcp 65001 >nul",
        command,
        "exit /b %ERRORLEVEL%",
      ].join("\r\n");
      await Zotero.File.putContentsAsync(scriptPath, script);
      args = ["/d", "/c", scriptPath];
    }

    const timeoutMs = this.getProcessTimeoutMs();

    return new Promise<void>((resolve, reject) => {
      const classes = Components.classes as any;
      const interfaces = Components.interfaces as any;

      const file = classes["@mozilla.org/file/local;1"].createInstance(
        interfaces.nsIFile,
      ) as any;
      file.initWithPath(shellPath);

      const process = classes["@mozilla.org/process/util;1"].createInstance(
        interfaces.nsIProcess,
      ) as any;
      process.init(file);

      let settled = false;
      const timer: { handle?: number } = {};
      const finish = (action: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        this.clearManagedTimeout(timer.handle);
        action();
      };

      const observer = {
        observe: (_subject: any, topic: string) => {
          if (topic === "process-finished" && process.exitValue === 0) {
            finish(resolve);
            return;
          }
          finish(() =>
            reject(
              new Error(
                `Command exited with code ${process.exitValue}. Command: ${command}`,
              ),
            ),
          );
        },
      };

      if (typeof process.runwAsync === "function") {
        process.runwAsync(args, args.length, observer);
      } else {
        process.runAsync(args, args.length, observer);
      }

      timer.handle = this.setManagedTimeout(() => {
        finish(() => {
          try {
            process.kill();
          } catch (_killError) {
            // Process may have already exited; ignore.
          }
          reject(
            new Error(
              `Command timed out after ${Math.round(timeoutMs / 1000)}s and was terminated. Command: ${command}`,
            ),
          );
        });
      }, timeoutMs);
    });
  }

  private static getProcessTimeoutMs() {
    const raw = String(getPref("processTimeoutSeconds") || "").trim();
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      return DEFAULT_PROCESS_TIMEOUT_MS;
    }
    return parsed * 1000;
  }

  private static async executeShellCommandWithOutput(command: string) {
    const outputPath = this.joinTempPath(
      `paper-flow-command-output-${Date.now()}.txt`,
    );
    const shellCommand = this.isWindows()
      ? `${command} > ${this.quoteForCmdArg(outputPath)} 2>&1`
      : `${command} > ${this.quoteForBashSingle(outputPath)} 2>&1`;
    try {
      await this.executeShellCommand(shellCommand);
      return String(await Zotero.File.getContentsAsync(outputPath));
    } catch (error) {
      let output = "";
      try {
        output = String(await Zotero.File.getContentsAsync(outputPath));
      } catch (_readError) {
        output = "(Paper Flow could not read the command output file.)";
      }
      const details = error instanceof Error ? error.message : String(error);
      throw new Error(
        [
          details,
          "",
          `OUTPUT_FILE:${outputPath}`,
          "",
          "COMMAND_OUTPUT:",
          output.trim() || "(no output captured)",
        ].join("\n"),
      );
    }
  }

  private static async buildDiagnosticMessage(options: {
    title: string;
    command: string;
    output: string;
  }) {
    const diagnostic = [
      options.title,
      `TIME:${new Date().toISOString()}`,
      `DIAGNOSTIC_FILE:${await this.writeDiagnosticFile(options)}`,
      "",
      "COMMAND:",
      this.maskSensitive(options.command),
      "",
      "OUTPUT:",
      this.maskSensitive(options.output),
    ].join("\n");
    return diagnostic;
  }

  private static async writeDiagnosticFile(options: {
    title: string;
    command: string;
    output: string;
  }) {
    const outputPath = this.joinTempPath("paper-flow-last-diagnostics.txt");
    const body = [
      options.title,
      `TIME:${new Date().toISOString()}`,
      "",
      "COMMAND:",
      this.maskSensitive(options.command),
      "",
      "OUTPUT:",
      this.maskSensitive(options.output),
    ].join("\n");
    await Zotero.File.putContentsAsync(outputPath, body);
    addon.data.runtime.lastDiagnosticPath = outputPath;
    return outputPath;
  }

  private static maskSensitive(value: string) {
    let result = value;
    for (const secret of [this.getNotionToken()]) {
      if (!secret) {
        continue;
      }
      result = result.split(secret).join("***");
    }
    return result;
  }

  private static copyTextToClipboard(text: string) {
    new ztoolkit.Clipboard().addText(text, "text/unicode").copy();
  }

  private static showProgress(
    text: string,
    type: "default" | "success" | "error",
  ) {
    new ztoolkit.ProgressWindow(config.addonName, {
      closeOnClick: true,
      closeTime: 6000,
    })
      .createLine({
        text,
        type,
        progress: type === "default" ? 30 : 100,
      })
      .show();
  }
}
