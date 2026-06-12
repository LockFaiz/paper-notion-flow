import { config } from "../package.json";
import { DialogHelper } from "zotero-plugin-toolkit";
import hooks from "./hooks";
import { createZToolkit } from "./utils/ztoolkit";

class Addon {
  public data: {
    alive: boolean;
    config: typeof config;
    // Env type, see build.js
    env: "development" | "production";
    initialized?: boolean;
    ztoolkit: ZToolkit;
    locale?: {
      current: any;
    };
    prefs?: {
      window: Window;
      updateStatus?: (message: string) => void;
    };
    dialog?: DialogHelper;
    runtime: {
      notifierID?: string;
      lastStatus: string;
      lastDiagnosticPath?: string;
      prefPaneID?: string;
    };
  };
  // Lifecycle hooks
  public hooks: typeof hooks;
  // APIs
  public api: object;

  constructor() {
    this.data = {
      alive: true,
      config,
      env: __env__,
      initialized: false,
      ztoolkit: createZToolkit(),
      runtime: {
        lastStatus: "Paper Flow is ready.",
      },
    };
    this.hooks = hooks;
    this.api = {};
  }
}

export default Addon;
