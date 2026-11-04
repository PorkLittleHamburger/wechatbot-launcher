export type Scope = { operation: string; id?: string };
export type Confirm = (title: string, text: string, key: string, action: (operationId: string) => Promise<unknown>, scope?: Scope) => void;
