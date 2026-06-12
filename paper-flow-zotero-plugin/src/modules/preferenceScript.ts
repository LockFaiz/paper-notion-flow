import { config } from "../../package.json";
import { getString, initLocale } from "../utils/locale";
import { getPref, setPref } from "../utils/prefs";
import { PaperFlowPlugin } from "./paperFlow";

export async function registerPrefsScripts(window: Window) {
  // Rebuild the Fluent bundle so getString-based strings (banner, config line)
  // follow a Zotero language switch like the data-l10n-id strings do — the
  // bundle cached at startup keeps the old locale otherwise.
  initLocale();
  addon.data.prefs = {
    window,
    updateStatus: (message: string) => {
      const statusNode = addon.data.prefs?.window.document.querySelector(
        `#zotero-prefpane-${config.addonRef}-status`,
      ) as HTMLTextAreaElement | null;
      if (statusNode) {
        statusNode.value = message;
      }
    },
  };

  addon.data.prefs.updateStatus?.(PaperFlowPlugin.getStatusMessage());
  syncManagedPreferenceUI(window);
  refreshModelOptions(window);
  refreshEffortOptions(window);
  refreshPromptPresets(window);
  bindPrefEvents(window);
  // Check CLI freshness + official model caches in the background so the
  // banner and the model dropdown are current every time the pane opens.
  void refreshCliBannerAndModels(window);
}

let cliRefreshRunning = false;
// One auto-update attempt per tool per pane session — prevents an endless
// update->recheck->update loop when an update keeps failing.
const autoUpdateAttempted = new Set<string>();

async function refreshCliBannerAndModels(window: Window) {
  if (cliRefreshRunning) {
    return;
  }
  cliRefreshRunning = true;
  setCliBanner(window, "checking", {});
  try {
    const result = await PaperFlowPlugin.refreshCliAndModels();
    if (!result) {
      setCliBanner(window, "unknown", {});
    } else if (!result.current || !result.latest) {
      setCliBanner(window, "unknown", { tool: result.tool });
    } else if (versionNumber(result.current) === result.latest.trim()) {
      setCliBanner(window, "ok", result);
    } else {
      setCliBanner(window, "outdated", result);
      // Outdated + auto-update enabled: update right now instead of asking
      // the user to click anything, then re-check so the banner turns green.
      if (getPref("autoUpdateCli") && !autoUpdateAttempted.has(result.tool)) {
        autoUpdateAttempted.add(result.tool);
        cliRefreshRunning = false;
        await PaperFlowPlugin.updateAiCli("auto");
        await refreshCliBannerAndModels(window);
        return;
      }
    }
    refreshModelOptions(window);
    refreshEffortOptions(window);
    updateConfigLine(window);
  } finally {
    cliRefreshRunning = false;
  }
}

function versionNumber(text: string) {
  return text.match(/\d+(?:\.\d+){1,3}/)?.[0] ?? text.trim();
}

function setCliBanner(
  window: Window,
  state: "checking" | "ok" | "outdated" | "unknown",
  info: { tool?: string; current?: string; latest?: string },
) {
  const banner = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-cli-banner`,
  ) as HTMLElement | null;
  if (!banner) {
    return;
  }
  banner.setAttribute("data-state", state);
  // getString resolves synchronously from the addon Fluent bundle, so the text
  // is guaranteed to render (the async Fluent-DOM path left the banner empty).
  banner.textContent = getString(`cli-${state}` as any, {
    args: {
      tool: info.tool === "claude" ? "Claude Code" : "Codex CLI",
      current: versionNumber(info.current || ""),
      latest: (info.latest || "").trim(),
    },
  });
}

function setBusy(window: Window, busy: boolean) {
  const root = window.document.querySelector(".pf-root") as HTMLElement | null;
  if (root) {
    if (busy) {
      root.setAttribute("data-busy", "true");
    } else {
      root.removeAttribute("data-busy");
    }
  }
}

async function withBusy(window: Window, action: () => Promise<void>) {
  setBusy(window, true);
  try {
    await action();
  } finally {
    setBusy(window, false);
  }
}

function onClick(window: Window, id: string, handler: () => void) {
  window.document
    .querySelector(`#zotero-prefpane-${config.addonRef}-${id}`)
    ?.addEventListener("click", handler);
}

function bindPrefEvents(window: Window) {
  onClick(window, "run-selected", () => {
    void withBusy(window, () => PaperFlowPlugin.processSelectedItems("manual"));
  });

  onClick(window, "prune-deleted", () => {
    void withBusy(window, () =>
      PaperFlowPlugin.pruneDeletedItems("manual", true),
    );
  });

  onClick(window, "archive-deleted", () => {
    void withBusy(window, () =>
      PaperFlowPlugin.pruneDeletedItems("manual", false),
    );
  });

  onClick(window, "use-selected-collection", () => {
    const collection =
      Zotero.getMainWindow()?.ZoteroPane?.getSelectedCollection?.();
    if (!collection) {
      PaperFlowPlugin.setStatusMessage("Select a Zotero collection first.");
      return;
    }
    setPref("watchedCollection" as any, collection.name as any);
    setPref("watchedCollectionKeys" as any, collection.key as any);
    syncManagedPreferenceUI(window);
    PaperFlowPlugin.setStatusMessage(
      `Watching Zotero collection: ${collection.name}`,
    );
  });

  onClick(window, "clear-watched-collection", () => {
    setPref("watchedCollection" as any, "" as any);
    setPref("watchedCollectionKeys" as any, "" as any);
    syncManagedPreferenceUI(window);
    PaperFlowPlugin.setStatusMessage(
      "Paper Flow will process all collections.",
    );
  });

  onClick(window, "workspace-browse", () => {
    void (async () => {
      // Zotero 7's native folder picker; returns an OS-style path which the
      // runtime-aware conversion maps to WSL form automatically when needed.
      const { FilePicker } = (
        ztoolkit.getGlobal("ChromeUtils") as any
      ).importESModule("chrome://zotero/content/modules/filePicker.mjs");
      const picker = new FilePicker();
      picker.init(
        window as any,
        getString("pick-workspace" as any),
        picker.modeGetFolder,
      );
      if ((await picker.show()) === picker.returnOK && picker.file) {
        setPref("workspacePath", String(picker.file) as any);
        syncManagedPreferenceUI(window);
        const input = window.document.querySelector(
          `#zotero-prefpane-${config.addonRef}-workspace-path`,
        ) as HTMLInputElement | null;
        if (input) {
          input.value = String(picker.file);
        }
      }
    })();
  });

  onClick(window, "check-ai", () => {
    void withBusy(window, () => PaperFlowPlugin.checkAiSetup());
  });

  onClick(window, "copy-example", () => {
    new ztoolkit.Clipboard()
      .addText(PaperFlowPlugin.getExampleCommand(), "text/unicode")
      .copy();
    PaperFlowPlugin.setStatusMessage(
      "Copied the current Paper Flow command to the clipboard.",
    );
  });

  onClick(window, "copy-status", () => {
    PaperFlowPlugin.copyStatusToClipboard();
  });

  onClick(window, "fill-default-prompt", () => {
    const defaultName = PaperFlowPlugin.getDefaultPromptName();
    setPromptOverride(window, PaperFlowPlugin.getActiveDefaultPrompt());
    PaperFlowPlugin.setStatusMessage(
      defaultName
        ? `Filled your default preset "${defaultName}" into the editor.`
        : "No default preset is set, so the built-in template was filled in.",
    );
  });

  onClick(window, "clear-custom-prompt", () => {
    setPromptOverride(window, "");
    PaperFlowPlugin.setStatusMessage(
      "Custom prompt cleared. Paper Flow will use the built-in default prompt.",
    );
  });

  const aiToolNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-ai-tool`,
  ) as XUL.MenuList | null;
  if (aiToolNode) {
    aiToolNode.addEventListener("command", () => {
      const value = aiToolNode.value;
      setPref("aiTool", value as any);
      if (value === "claude") {
        setPref("aiArgs", "--print" as any);
      } else {
        setPref("aiArgs", "exec --skip-git-repo-check" as any);
      }
      // Model options differ per CLI, so reset to the default when switching.
      setPref("aiModel", "" as any);
      syncManagedPreferenceUI(window);
      // Rebuilding ANOTHER menulist's popup is safe; rebuilding a menulist's
      // own popup from its own command/change handler destroys the popup and
      // it can never be opened again — hence rebuilds live here, not in the
      // model/effort handlers themselves.
      refreshModelOptions(window);
      refreshEffortOptions(window);
      // The banner reports the SELECTED tool's version, so switching tools
      // must re-run the check — otherwise it keeps showing the previous CLI.
      void refreshCliBannerAndModels(window);
    });
  }

  const modelNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-ai-model`,
  ) as XUL.MenuList | null;
  if (modelNode) {
    const saveModel = () => {
      setPref("aiModel", String(modelNode.value || "").trim() as any);
      // Do NOT rebuild the model popup here (see comment above). Effort levels
      // depend on the selected codex model, so rebuild that one.
      refreshEffortOptions(window);
      updateConfigLine(window);
    };
    // "command" fires on dropdown selection; "change" fires when the user
    // types a custom model (e.g. a just-released alias) into the editable box.
    modelNode.addEventListener("command", saveModel);
    modelNode.addEventListener("change", saveModel);
  }

  onClick(window, "update-cli", () => {
    void withBusy(window, async () => {
      await PaperFlowPlugin.updateAiCli("manual");
      // Re-check so the banner flips to green right after a successful update.
      await refreshCliBannerAndModels(window);
    });
  });
  onClick(window, "refresh-models", () => {
    void withBusy(window, () => refreshCliBannerAndModels(window));
  });

  const executionModeNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-execution-mode`,
  ) as XUL.MenuList | null;
  if (executionModeNode) {
    executionModeNode.addEventListener("command", () => {
      setPref("executionMode", executionModeNode.value as any);
      syncManagedPreferenceUI(window);
    });
  }

  const guideLanguageNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-guide-language`,
  ) as XUL.MenuList | null;
  if (guideLanguageNode) {
    guideLanguageNode.addEventListener("command", () => {
      setPref("guideLanguage", guideLanguageNode.value as any);
      syncManagedPreferenceUI(window);
    });
  }

  const aiEffortNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-ai-effort`,
  ) as XUL.MenuList | null;
  if (aiEffortNode) {
    aiEffortNode.addEventListener("command", () => {
      setPref("aiEffort", aiEffortNode.value as any);
      // No popup rebuild here — rebuilding this menulist's own popup from its
      // command handler would break it permanently.
      updateConfigLine(window);
    });
  }

  const liveInputs: Array<[string, string]> = [
    ["notion-token", "notionToken"],
    ["notion-database-id", "notionDatabaseId"],
    ["watched-collection", "watchedCollection"],
    ["ai-extra-args", "aiExtraArgs"],
    ["ai-args", "aiArgs"],
  ];
  for (const [id, prefName] of liveInputs) {
    const inputNode = window.document.querySelector(
      `#zotero-prefpane-${config.addonRef}-${id}`,
    ) as HTMLInputElement | null;
    if (inputNode) {
      const save = () => {
        setPref(prefName as any, inputNode.value as any);
        syncManagedPreferenceUI(window);
      };
      inputNode.addEventListener("input", save);
      inputNode.addEventListener("change", save);
    }
  }

  const promptNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-prompt-override`,
  ) as HTMLTextAreaElement | null;
  if (promptNode) {
    const savePrompt = () => {
      setPref("promptOverride", promptNode.value as any);
    };
    promptNode.addEventListener("input", savePrompt);
    promptNode.addEventListener("change", savePrompt);
  }

  bindPromptPresetEvents(window);
}

function getPresetNode(window: Window): XUL.MenuList | null {
  return window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-prompt-presets`,
  ) as XUL.MenuList | null;
}

function findPresetText(name: string): string {
  return (
    PaperFlowPlugin.getPromptPresets().find((preset) => preset.name === name)
      ?.text ?? ""
  );
}

function bindPromptPresetEvents(window: Window) {
  const presetNode = getPresetNode(window);
  if (presetNode) {
    presetNode.addEventListener("command", () => {
      const name = presetNode.value;
      if (!name) {
        return;
      }
      setPromptOverride(window, findPresetText(name));
      PaperFlowPlugin.setStatusMessage(`Loaded prompt preset: ${name}`);
    });
  }

  onClick(window, "save-preset", () => {
    const node = window.document.querySelector(
      `#zotero-prefpane-${config.addonRef}-prompt-override`,
    ) as HTMLTextAreaElement | null;
    const name = PaperFlowPlugin.savePromptPreset(node?.value || "");
    if (!name) {
      PaperFlowPlugin.setStatusMessage(
        "The prompt editor is empty, so there is nothing to save.",
      );
      return;
    }
    refreshPromptPresets(window);
    const presets = getPresetNode(window);
    if (presets) {
      presets.value = name;
    }
    PaperFlowPlugin.setStatusMessage(`Saved prompt preset: ${name}`);
  });

  onClick(window, "set-default-preset", () => {
    const presets = getPresetNode(window);
    const name = presets?.value;
    if (!name) {
      PaperFlowPlugin.setStatusMessage(
        "Select a saved prompt first, then set it as default.",
      );
      return;
    }
    PaperFlowPlugin.setDefaultPromptPreset(name);
    setPromptOverride(window, findPresetText(name));
    refreshPromptPresets(window);
    if (presets) {
      presets.value = name;
    }
    PaperFlowPlugin.setStatusMessage(
      `Default prompt set to "${name}". It is re-applied on every startup.`,
    );
  });

  onClick(window, "delete-preset", () => {
    const presets = getPresetNode(window);
    const name = presets?.value;
    if (!name) {
      PaperFlowPlugin.setStatusMessage("Select a saved prompt to delete.");
      return;
    }
    PaperFlowPlugin.deletePromptPreset(name);
    refreshPromptPresets(window);
    PaperFlowPlugin.setStatusMessage(`Deleted prompt preset: ${name}`);
  });
}

function refreshPromptPresets(window: Window) {
  const menulist = getPresetNode(window);
  const popup = menulist?.querySelector("menupopup");
  if (!menulist || !popup) {
    return;
  }
  const presets = PaperFlowPlugin.getPromptPresets();
  const defaultName = PaperFlowPlugin.getDefaultPromptName();
  const doc = window.document as any;
  while (popup.firstChild) {
    popup.removeChild(popup.firstChild);
  }
  const placeholder = doc.createXULElement("menuitem");
  placeholder.setAttribute("value", "");
  placeholder.setAttribute(
    "label",
    presets.length ? "—" : "(no saved prompts yet)",
  );
  popup.appendChild(placeholder);
  for (const preset of presets) {
    const item = doc.createXULElement("menuitem");
    item.setAttribute("value", preset.name);
    item.setAttribute(
      "label",
      `${preset.name === defaultName ? "★ " : ""}${preset.name}`,
    );
    popup.appendChild(item);
  }
  menulist.value =
    defaultName && presets.some((preset) => preset.name === defaultName)
      ? defaultName
      : "";
}

function refreshModelOptions(window: Window) {
  const menulist = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-ai-model`,
  ) as XUL.MenuList | null;
  const popup = menulist?.querySelector("menupopup");
  if (!menulist || !popup) {
    return;
  }
  const options = PaperFlowPlugin.getModelOptions();
  const current = String(getPref("aiModel") || "");
  const doc = window.document as any;
  while (popup.firstChild) {
    popup.removeChild(popup.firstChild);
  }
  for (const option of options) {
    const item = doc.createXULElement("menuitem");
    item.setAttribute("value", option.value);
    item.setAttribute("label", option.label);
    popup.appendChild(item);
  }
  // Keep the saved value selectable even if it is not in the suggested list.
  if (current && !options.some((option) => option.value === current)) {
    const item = doc.createXULElement("menuitem");
    item.setAttribute("value", current);
    item.setAttribute("label", `${current} (saved)`);
    popup.appendChild(item);
  }
  menulist.value = current;
}

/**
 * Effort levels follow the selected CLI/model: codex levels come from the
 * discovered models cache, claude uses its documented set.
 */
function refreshEffortOptions(window: Window) {
  const menulist = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-ai-effort`,
  ) as XUL.MenuList | null;
  const popup = menulist?.querySelector("menupopup");
  if (!menulist || !popup) {
    return;
  }
  const levels = PaperFlowPlugin.getEffortOptions();
  const current = String(getPref("aiEffort") || "");
  const doc = window.document as any;
  while (popup.firstChild) {
    popup.removeChild(popup.firstChild);
  }
  const defaultItem = doc.createXULElement("menuitem");
  defaultItem.setAttribute("value", "");
  defaultItem.setAttribute("label", getString("effort-default" as any));
  popup.appendChild(defaultItem);
  for (const level of levels) {
    const item = doc.createXULElement("menuitem");
    item.setAttribute("value", level);
    item.setAttribute("label", level);
    popup.appendChild(item);
  }
  // A saved level not supported by the current model stays selectable so the
  // user sees what is configured instead of a silently blank control.
  if (current && !levels.includes(current)) {
    const item = doc.createXULElement("menuitem");
    item.setAttribute("value", current);
    item.setAttribute("label", `${current} (saved)`);
    popup.appendChild(item);
  }
  menulist.value = current;
}

function formatContextWindow(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${Math.round(tokens / 100_000) / 10}M`;
  }
  return `${Math.round(tokens / 1000)}K`;
}

/**
 * One always-visible line summarising exactly what will run: tool, model
 * (resolving "default" to codex's configured model when known), reasoning
 * effort, and the context window when the official cache reports it.
 */
function updateConfigLine(window: Window) {
  const node = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-config-line`,
  ) as HTMLElement | null;
  if (!node) {
    return;
  }
  const tool =
    String(getPref("aiTool") || "codex").toLowerCase() === "claude"
      ? "claude"
      : "codex";
  const discovered = PaperFlowPlugin.getDiscoveredModels();
  const model = String(getPref("aiModel") || "").trim();
  const effort = String(getPref("aiEffort") || "").trim();
  const defaultText = getString("effort-default" as any);

  let modelLabel = model;
  if (!model) {
    modelLabel =
      tool === "codex" && discovered.codex_current
        ? `${discovered.codex_current} (${defaultText})`
        : defaultText;
  }

  let context = "";
  if (tool === "codex") {
    const effective = model || discovered.codex_current || "";
    const entry = (
      discovered.codex as Array<{ value: string; context?: number }>
    ).find((item) => item.value === effective);
    if (entry?.context) {
      context = formatContextWindow(entry.context);
    }
  } else if (/\[1m\]$/i.test(model)) {
    context = "1M";
  }

  let text = getString("config-line" as any, {
    args: {
      tool: tool === "claude" ? "Claude Code" : "Codex CLI",
      model: modelLabel,
      effort: effort || defaultText,
    },
  });
  if (context) {
    text += getString("config-context" as any, { args: { context } });
  }
  node.textContent = text;
}

function setPromptOverride(window: Window, value: string) {
  setPref("promptOverride", value as any);
  const promptNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-prompt-override`,
  ) as HTMLTextAreaElement | null;
  if (promptNode) {
    promptNode.value = value;
  }
}

function syncManagedPreferenceUI(window: Window) {
  const executionModeNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-execution-mode`,
  ) as XUL.MenuList | null;
  if (executionModeNode) {
    executionModeNode.value = String(getPref("executionMode") || "auto");
  }

  const guideLanguageNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-guide-language`,
  ) as XUL.MenuList | null;
  if (guideLanguageNode) {
    guideLanguageNode.value = String(getPref("guideLanguage") || "zh-CN");
  }

  const wslRow = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-wsl-row`,
  ) as HTMLElement | null;
  if (wslRow) {
    const executionMode = String(getPref("executionMode") || "auto");
    wslRow.hidden = !Zotero.isWin || executionMode === "native";
  }

  const aiToolNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-ai-tool`,
  ) as XUL.MenuList | null;
  if (aiToolNode) {
    aiToolNode.value = String(getPref("aiTool") || "codex");
  }

  const notionTokenNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-notion-token`,
  ) as HTMLInputElement | null;
  if (notionTokenNode) {
    notionTokenNode.value = String(getPref("notionToken") || "");
  }

  const notionDatabaseIdNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-notion-database-id`,
  ) as HTMLInputElement | null;
  if (notionDatabaseIdNode) {
    notionDatabaseIdNode.value = String(getPref("notionDatabaseId") || "");
  }

  const watchedCollectionNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-watched-collection`,
  ) as HTMLInputElement | null;
  if (watchedCollectionNode) {
    watchedCollectionNode.value = String(getPref("watchedCollection") || "");
  }

  const aiArgsNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-ai-args`,
  ) as HTMLInputElement | null;
  if (aiArgsNode) {
    aiArgsNode.value = String(getPref("aiArgs") || "");
  }

  // Popup rebuilds intentionally do NOT happen here: this function runs inside
  // many control event handlers, and rebuilding an open menulist's own popup
  // kills it. Rebuilds are done explicitly at load / tool switch / discovery.
  updateConfigLine(window);

  const aiExtraArgsNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-ai-extra-args`,
  ) as HTMLInputElement | null;
  if (aiExtraArgsNode) {
    aiExtraArgsNode.value = String(getPref("aiExtraArgs") || "");
  }

  const promptNode = window.document.querySelector(
    `#zotero-prefpane-${config.addonRef}-prompt-override`,
  ) as HTMLTextAreaElement | null;
  if (promptNode) {
    promptNode.value = String(getPref("promptOverride") || "");
  }
}
