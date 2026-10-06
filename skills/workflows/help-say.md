# Skill: Help Say

Version: 1.0

## Objective

Создай готовое сообщение адресату на основании намерения пользователя, фактов и правил конкретных отношений.

## Required inputs

- user intent / rough draft;
- RelationshipContext;
- SafetyDecision instructions;
- GenerationPlan.

## Preserve

- desired outcome;
- explicit facts;
- non-negotiable boundaries;
- requested firmness;
- relationship rules.

## Do not

- добавлять обещания от имени пользователя;
- добавлять чувства, которых пользователь не обозначал;
- invent facts;
- делать сообщение длиннее без необходимости;
- ослаблять основную позицию только ради вежливости.

## Output

Верни `HelpSayResult`.

Поле `preserved_intent` обязательно и не может быть пустым: кратко опиши исходное намерение пользователя, которое сохранено в сообщении.
