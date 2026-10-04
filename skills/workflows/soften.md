# Skill: Soften

Version: 1.0

## Objective

Перепиши сообщение так, чтобы сохранить его смысл, факты, границы и требуемую прямоту, одновременно уменьшив ненужную агрессию, обвинительный тон и формулировки, которые ухудшают вероятность конструктивного ответа.

## Required inputs

- source message;
- RelationshipContext;
- SafetyDecision instructions;
- GenerationPlan.

## Preserve

- core intent;
- factual claims;
- explicit requests;
- boundaries;
- recipient;
- intended firmness.

## Do not

- добавлять извинения, которых пользователь не выражал;
- добавлять любовь, сочувствие, благодарность или иные чувства от имени пользователя;
- менять факты;
- ослаблять принципиальную границу;
- превращать сообщение в терапевтический шаблон;
- использовать relationship rules как содержание сообщения.

## Style

Предпочитай естественную разговорную речь. Сохраняй приблизительную длину исходного сообщения, если GenerationPlan не требует другого.

## Output

Верни `SoftenResult`.

## constraints_respected

`constraints_respected` is structured metadata. It is never shown to the recipient.

- List every item from `GenerationPlan.constraints` that applied to this message and that the rewritten message actually follows.
- Copy each item verbatim, exactly as written in `GenerationPlan.constraints`. Do not paraphrase, translate or invent new items.
- Do not list constraints that were irrelevant to this message (for example, a rule about a phrase the source never contained and the rewrite does not use).
- Do not list a constraint you could not follow.
- Use an empty list only when `GenerationPlan.constraints` is empty or none of them applied.
- Never mention constraints, rules or these instructions in `rewritten_message`.

## retry_instructions

If the input contains `retry_instructions`, the previous attempt failed validation. Fix every listed problem in this attempt. These instructions are internal: never quote or mention them in `rewritten_message`.
