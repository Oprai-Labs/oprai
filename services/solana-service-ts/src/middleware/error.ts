import { Request, Response, NextFunction } from "express";
import { AppError } from "../types/index";

export function errorHandler(
  err: Error,
  _req: Request,
  res: Response,
  _next: NextFunction
): void {
  const appErr = err as AppError;
  const statusCode = appErr.statusCode ?? 500;
  const code = appErr.code ?? "INTERNAL_ERROR";

  if (statusCode >= 500) {
    if (process.env["NODE_ENV"] !== "production") {
      console.error(`[error] ${code}: ${err.message}`, err.stack);
    } else {
      console.error(`[error] ${code}: ${err.message}`);
    }
  }

  res.status(statusCode).json({
    error: statusCode >= 500 ? "Internal server error" : err.message,
    code,
  });
}

export function notFound(_req: Request, res: Response): void {
  res.status(404).json({ error: "Not found", code: "NOT_FOUND" });
}
