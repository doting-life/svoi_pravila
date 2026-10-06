import { t, type Language } from "../../i18n";
import { Button, Card, Notice, Stack } from "../../components/ui";
import { getBotLink, openTelegramLink } from "../../telegram";

export function ConsentRequiredScreen({ language, onRetry }: { language: Language; onRetry?: () => void }) {
  const botLink = getBotLink();
  return (
    <Card header={t(language, "consentRequiredTitle")}>
      <Stack>
        <Notice>{t(language, "consentRequiredText")}</Notice>
        <Button
          onClick={() => {
            openTelegramLink(botLink);
          }}
        >
          {t(language, "consentRequiredOpenBot")}
        </Button>
        <a href={botLink} target="_blank" rel="noopener noreferrer">
          {botLink}
        </a>
        {onRetry ? (
          <Button mode="bezeled" onClick={onRetry}>
            {t(language, "consentRequiredRetry")}
          </Button>
        ) : null}
      </Stack>
    </Card>
  );
}
