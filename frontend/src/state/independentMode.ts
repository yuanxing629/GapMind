/** 支撑 W7 standalone 对话的系统 workspace。 */
export const INDEPENDENT_WORKSPACE_NAME = "__independent__";

export function isIndependentWorkspaceName(name?: string): boolean {
  return name === INDEPENDENT_WORKSPACE_NAME;
}
