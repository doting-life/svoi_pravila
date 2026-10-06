import "@telegram-apps/telegram-ui/dist/styles.css";
import "./select.css";

import {
  AppRoot,
  Cell as TgCell,
  List as TgList,
  Modal as TgModal,
  Section as TgSection,
  Spinner as TgSpinner,
} from "@telegram-apps/telegram-ui";
import type { ComponentProps, PropsWithChildren, ReactNode } from "react";

export function UiRoot({ children }: PropsWithChildren) {
  return <AppRoot>{children}</AppRoot>;
}

export function Card({ header, children }: PropsWithChildren<{ header?: string }>) {
  return <TgSection header={header}>{children}</TgSection>;
}

export function Row({ children }: PropsWithChildren) {
  return <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>{children}</div>;
}

export function Stack({ children }: PropsWithChildren) {
  return <div style={{ display: "grid", gap: 12, padding: 12 }}>{children}</div>;
}

export function List({ children }: PropsWithChildren) {
  return <TgList>{children}</TgList>;
}

export function ListSection({
  header,
  footer,
  children,
}: PropsWithChildren<{ header?: ReactNode; footer?: ReactNode }>) {
  return (
    <TgSection header={header} footer={footer}>
      {children}
    </TgSection>
  );
}

export function ListCell(props: ComponentProps<typeof TgCell>) {
  return <TgCell multiline {...props} />;
}

export function Spinner({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} style={{ display: "flex", justifyContent: "center", padding: 24 }}>
      <TgSpinner size="m" />
    </div>
  );
}

export function Modal({ open, onClose, children }: PropsWithChildren<{ open: boolean; onClose: () => void }>) {
  return (
    <TgModal
      open={open}
      onOpenChange={(nextOpen: boolean) => {
        if (!nextOpen) {
          onClose();
        }
      }}
    >
      <div style={{ padding: 16 }}>{children}</div>
    </TgModal>
  );
}

export function Notice({ children, tone = "info" }: PropsWithChildren<{ tone?: "info" | "error" }>) {
  return (
    <p
      role={tone === "error" ? "alert" : "note"}
      style={{ margin: 0, color: tone === "error" ? "var(--tgui--destructive_text_color)" : "var(--tgui--hint_color)" }}
    >
      {children}
    </p>
  );
}
