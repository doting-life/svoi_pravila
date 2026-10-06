import { HashRouter, Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { isConsentRequiredError, toErrorStatus } from "../api/errors";
import { ErrorState, LoadingState } from "../components/common";
import { Button, Row } from "../components/ui";
import { MainScreen } from "../features/assist/MainScreen";
import { ConsentRequiredScreen } from "../features/consent/ConsentRequiredScreen";
import { OnboardingScreen } from "../features/onboarding/OnboardingScreen";
import { RelationshipDetailsScreen } from "../features/relationships/RelationshipDetailsScreen";
import { RelationshipsScreen } from "../features/relationships/RelationshipsScreen";
import { SettingsScreen } from "../features/settings/SettingsScreen";
import { useBootstrapQuery } from "../state/queries";
import { useAppLanguage, useT } from "./AppProviders";

export function BootstrapGate() {
  const { language } = useAppLanguage();
  const location = useLocation();
  const bootstrap = useBootstrapQuery();

  if (bootstrap.isPending) {
    return <LoadingState language={language} />;
  }
  if (bootstrap.isError && isConsentRequiredError(bootstrap.error)) {
    return (
      <ConsentRequiredScreen
        language={language}
        onRetry={() => {
          void bootstrap.refetch();
        }}
      />
    );
  }
  if (bootstrap.isError) {
    return (
      <ErrorState
        language={language}
        status={toErrorStatus(bootstrap.error)}
        onRetry={() => {
          void bootstrap.refetch();
        }}
      />
    );
  }
  if (bootstrap.data.relationships.length === 0 && location.pathname !== "/onboarding") {
    return <Navigate to="/onboarding" replace />;
  }
  return <Outlet />;
}

function NavigationBar() {
  const dict = useT();
  const location = useLocation();
  const navigate = useNavigate();
  const items: { path: string; label: string; active: boolean }[] = [
    { path: "/", label: dict.navMain, active: location.pathname === "/" },
    { path: "/relationships", label: dict.relationships, active: location.pathname.startsWith("/relationships") },
    { path: "/settings", label: dict.settings, active: location.pathname === "/settings" },
  ];
  return (
    <nav aria-label={dict.appTitle} style={{ padding: 12 }}>
      <Row>
        {items.map((item) => (
          <Button
            key={item.path}
            mode={item.active ? "filled" : "bezeled"}
            aria-current={item.active ? "page" : undefined}
            onClick={() => navigate(item.path)}
          >
            {item.label}
          </Button>
        ))}
      </Row>
    </nav>
  );
}

function AppLayout() {
  return (
    <>
      <NavigationBar />
      <main>
        <Outlet />
      </main>
    </>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<BootstrapGate />}>
        <Route path="/onboarding" element={<OnboardingScreen />} />
        <Route element={<AppLayout />}>
          <Route path="/" element={<MainScreen />} />
          <Route path="/relationships" element={<RelationshipsScreen />} />
          <Route path="/relationships/:relationshipId" element={<RelationshipDetailsScreen />} />
          <Route path="/settings" element={<SettingsScreen />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function AppRouter() {
  return (
    <HashRouter>
      <AppRoutes />
    </HashRouter>
  );
}
