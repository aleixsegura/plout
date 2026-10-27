import { Navigate, Route, Routes } from "react-router-dom";
import { AppDataProvider } from "./data";
import AppLayout from "./AppLayout";
import DashboardPage from "./DashboardPage";
import HoldingsPage from "./HoldingsPage";
import TransactionsPage from "./TransactionsPage";
import DividendsPage from "./DividendsPage";
import TaxesPage from "./TaxesPage";
import CorrelationPage from "./CorrelationPage";

export default function App() {
  return (
    <AppDataProvider>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="holdings" element={<HoldingsPage />} />
          <Route path="transactions" element={<TransactionsPage />} />
          <Route path="dividends" element={<DividendsPage />} />
          <Route path="taxes" element={<TaxesPage />} />
          <Route path="correlation" element={<CorrelationPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </AppDataProvider>
  );
}
