# AI prompts

No model is called yet: phases 0–2 are AI-free by design (spec §14). Prompts arrive in phase 3 as
versioned files in `lib/ai/prompts/*.ts` (each exporting `PROMPT_VERSION`) and their full text is
mirrored here.

| Prompt                                                                           | Model env          | Phase | Status      |
| -------------------------------------------------------------------------------- | ------------------ | ----- | ----------- |
| `generatePlan` (§9.2)                                                            | `AI_MODEL_PLANNER` | 3     | not started |
| `replan` (§9.3)                                                                  | `AI_MODEL_PLANNER` | 3     | not started |
| assistant system prompt + tools (§9.4)                                           | `AI_MODEL_PLANNER` | 3     | not started |
| `mapIngredientLocal`, `estimateNutrition`, `extractLabel`, `importRecipe` (§9.5) | `AI_MODEL_FAST`    | 3     | not started |
| `channelPost` (§9.5)                                                             | `AI_MODEL_FAST`    | 4     | not started |

Already in place for them: the hard-rule validator (`lib/domain/rules.ts`), Zod schemas for recipe
structure (`lib/seed-schema.ts`), and the `ai_calls` table for token/cost accounting.
