/**
 * Plugin System Interface
 *
 * Defines the contract for plugins that can extend agent capabilities.
 * Based on elizaOS plugin architecture.
 */

import { Observable } from 'rxjs';

/**
 * Plugin manifest metadata
 */
export interface PluginManifest {
  /** Unique plugin identifier */
  id: string;

  /** Display name */
  name: string;

  /** Plugin version */
  version: string;

  /** Plugin description */
  description?: string;

  /** Author information */
  author?: string;

  /** Required dependencies */
  dependencies?: string[];

  /** Supported clients */
  clients?: string[];

  /** Plugin category */
  category?: 'protocol' | 'utility' | 'social' | 'market' | 'action';

  /** Plugin icon */
  icon?: string;

  /** Whether plugin is enabled by default */
  enabled?: boolean;
}

/**
 * Plugin configuration
 */
export interface PluginConfig {
  /** Whether plugin is enabled */
  enabled: boolean;

  /** Plugin-specific configuration */
  settings?: Record<string, unknown>;

  /** Priority (higher = executed first) */
  priority?: number;
}

/**
 * Action definition - a discrete action the agent can perform
 */
export interface PluginAction {
  /** Unique action identifier */
  id: string;

  /** Action name for display */
  name: string;

  /** Action description */
  description?: string;

  /** Action handler function */
  handler: string;

  /** Examples of when to use this action */
  examples?: string[];

  /** JSON schema for action parameters */
  schema?: any;

  /** Whether action is async */
  async?: boolean;
}

/**
 * Provider definition - fetches data from external sources
 */
export interface PluginProvider {
  /** Unique provider identifier */
  id: string;

  /** Provider name for display */
  name: string;

  /** Provider description */
  description?: string;

  /** Provider function name */
  getFunction: string;

  /** Cache TTL in seconds */
  cacheTtl?: number;

  /** Whether provider is async */
  async?: boolean;
}

/**
 * Evaluator definition - evaluates messages/responses
 */
export interface PluginEvaluator {
  /** Unique evaluator identifier */
  id: string;

  /** Evaluator name for display */
  name: string;

  /** Evaluator description */
  description?: string;

  /** Evaluator function name */
  evaluateFunction: string;

  /** Whether evaluator is async */
  async?: boolean;
}

/**
 * Client definition - platform integration (discord, Twitter, etc.)
 */
export interface PluginClient {
  /** Unique client identifier */
  id: string;

  /** Client name for display */
  name: string;

  /** Client type */
  type: 'discord' | 'twitter' | 'telegram' | 'farcaster' | 'slack' | 'direct';

  /** Client description */
  description?: string;

  /** Required configuration */
  config?: Record<string, unknown>;

  /** Whether client is async */
  async?: boolean;
}

/**
 * Event definition - triggered by actions or external events
 */
export interface PluginEvent {
  /** Event type */
  type: string;

  /** Event payload */
  payload: any;

  /** Timestamp */
  timestamp: Date;

  /** Source plugin ID */
  sourcePluginId?: string;
}

/**
 * Plugin interface - all plugins must implement this
 */
export interface IPlugin {
  /** Plugin manifest */
  manifest: PluginManifest;

  /** Plugin configuration */
  config: PluginConfig;

  /** Actions provided by this plugin */
  actions: PluginAction[];

  /** Providers provided by this plugin */
  providers: PluginProvider[];

  /** Evaluators provided by this plugin */
  evaluators: PluginEvaluator[];

  /** Clients provided by this plugin */
  clients: PluginClient[];

  /** Plugin initialization hook */
  onInit?: (context: PluginContext) => Promise<void> | void;

  /** Plugin destruction hook */
  onDestroy?: (context: PluginContext) => Promise<void> | void;

  /** Event handler */
  onEvent?: (event: PluginEvent, context: PluginContext) => Promise<void> | void;
}

/**
 * Plugin context passed to plugin hooks
 */
export interface PluginContext {
  /** Plugin configuration */
  config: PluginConfig;

  /** Shared state */
  state: Map<string, unknown>;

  /** Logger instance */
  logger: {
    info: (message: string, ...args: unknown[]) => void;
    warn: (message: string, ...args: unknown[]) => void;
    error: (message: string, ...args: unknown[]) => void;
    debug: (message: string, ...args: unknown[]) => void;
  };

  /** Character ID running this plugin */
  characterId: string;

  /** Runtime services */
  services: Map<string, unknown>;
}

/**
 * Plugin load result
 */
export interface PluginLoadResult {
  /** Plugin manifest */
  manifest: PluginManifest;

  /** Whether plugin loaded successfully */
  success: boolean;

  /** Error message if failed */
  error?: string;

  /** Time taken to load */
  loadTime?: number;
}

/**
 * Plugin execution result
 */
export interface PluginExecutionResult<T = unknown> {
  /** Whether execution was successful */
  success: boolean;

  /** Result data */
  data?: T;

  /** Error message if failed */
  error?: string;

  /** Execution time in ms */
  executionTime?: number;
}

/**
 * Plugin registry interface
 */
export interface IPluginRegistry {
  /** All registered plugins */
  plugins: Observable<IPlugin[]>;

  /** All registered actions */
  actions: Observable<Map<string, PluginAction>>;

  /** All registered providers */
  providers: Observable<Map<string, PluginProvider>>;

  /** All registered evaluators */
  evaluators: Observable<Map<string, PluginEvaluator>>;

  /** Load a plugin */
  load(manifest: PluginManifest): Promise<PluginLoadResult>;

  /** Unload a plugin */
  unload(pluginId: string): Promise<boolean>;

  /** Get a plugin by ID */
  getPlugin(id: string): IPlugin | undefined;

  /** Get an action by ID */
  getAction(id: string): PluginAction | undefined;

  /** Get a provider by ID */
  getProvider(id: string): PluginProvider | undefined;

  /** Execute an action */
  executeAction(actionId: string, params: any, context: PluginContext): Promise<PluginExecutionResult>;

  /** Execute a provider */
  executeProvider(providerId: string, params: any, context: PluginContext): Promise<PluginExecutionResult>;

  /** Emit an event */
  emitEvent(event: PluginEvent): void;
}
