// Public DTO names for the Mini App API.
//
// Every type here is an alias of the openapi-typescript output in
// ./generated/openapi.ts (generated from docs/openapi.json via
// `npm run generate`). Do not redeclare schema shapes by hand.

import type { components } from "./generated/openapi";

type Schemas = components["schemas"];

export type AuthResponse = Schemas["AuthResponse"];
export type BootstrapResponse = Schemas["BootstrapResponse"];
export type ConsentRequiredError = Schemas["ConsentRequiredError"];
export type DeliveryResponse = Schemas["DeliveryResponse"];
export type HTTPValidationError = Schemas["HTTPValidationError"];
export type MiniAppAssistBody = Schemas["MiniAppAssistBody"];
export type RelationshipCreateBody = Schemas["RelationshipCreateBody"];
export type RelationshipRule = Schemas["RelationshipRule"];
export type RelationshipUpdateBody = Schemas["RelationshipUpdateBody"];
export type RelationshipView = Schemas["RelationshipView"];
export type RuleBody = Schemas["RuleBody"];
export type UserView = Schemas["UserView"];
export type ValidationError = Schemas["ValidationError"];
export type WorkflowName = Schemas["WorkflowName"];

export type { components, operations, paths } from "./generated/openapi";

/** Must equal `info.version` in docs/openapi.json (asserted by the test suite). */
export const API_VERSION = "0.6.0";

/** Runtime list of WorkflowName values (asserted against the OpenAPI enum by the test suite). */
export const workflowNameValues = ["soften", "decode", "help-say"] as const satisfies readonly WorkflowName[];
