export const STATUS_COLORS: Record<string, string> = {
  confirmed: "bg-semantic-success/10 text-semantic-success border-semantic-success/20",
  pending: "bg-semantic-warning/10 text-semantic-warning border-semantic-warning/20",
  failed: "bg-semantic-danger/10 text-semantic-danger border-semantic-danger/20",
  submitted: "bg-semantic-info/10 text-semantic-info border-semantic-info/20",
};

export const USER_STATUS_OPTIONS = [
  { value: "active", label: "Active" },
  { value: "suspended", label: "Suspended" },
  { value: "banned", label: "Banned" },
];

export const ACTION_LABELS: Record<string, string> = {
  swap: "Swap",
  transfer: "Transfer",
  stake: "Stake",
  unstake: "Unstake",
  launch: "Launch",
};

export const AUDIT_ACTION_LABELS: Record<string, string> = {
  "user.status_change": "User Status Change",
  "user.role_change": "User Role Change",
  "session.close": "Session Closed",
  "admin.login": "Admin Login",
  "admin.create": "Admin Created",
  "admin.delete": "Admin Deleted",
  "admin.password_reset": "Password Reset",
  "data.export": "Data Export",
};

export const TX_STATUS_OPTIONS = [
  { value: "confirmed", label: "Confirmed" },
  { value: "pending", label: "Pending" },
  { value: "failed", label: "Failed" },
  { value: "submitted", label: "Submitted" },
];

export const TX_ACTION_OPTIONS = [
  { value: "swap", label: "Swap" },
  { value: "transfer", label: "Transfer" },
  { value: "stake", label: "Stake" },
  { value: "unstake", label: "Unstake" },
  { value: "launch", label: "Launch" },
];

// Indigo-accented chart palette
export const CHART_COLORS = [
  "hsl(250, 80%, 64%)",  // accent (indigo)
  "hsl(152, 60%, 52%)",  // success (emerald)
  "hsl(210, 90%, 60%)",  // info (blue)
  "hsl(38, 95%, 56%)",   // warning (amber)
  "hsl(0, 72%, 58%)",    // danger (rose)
  "hsl(190, 80%, 45%)",  // cyan
  "hsl(280, 65%, 60%)",  // purple
  "hsl(100, 55%, 50%)",  // lime
];

export const CHART_TOOLTIP_STYLE = {
  backgroundColor: "hsl(220, 14%, 7%)",
  border: "1px solid hsl(220, 12%, 14%)",
  borderRadius: "10px",
  boxShadow: "0 8px 32px -8px rgb(0 0 0 / 0.6)",
  fontSize: "12px",
  padding: "8px 12px",
};

export const AXIS_STYLE = {
  stroke: "hsl(220, 12%, 16%)",
  fontSize: 11,
  tickLine: false as const,
  axisLine: false as const,
};
