import type { Book } from "../api/client";

export const BOOKS: { book: Book; title: string; hint: string }[] = [
  { book: "asset", title: "Asset Book", hint: "Loans · .xlsx or .csv" },
  { book: "liability", title: "Liability Book", hint: "Deposits & borrowings · .xlsx or .csv" },
  { book: "historical", title: "Historical Log", hint: "Last 12 months, for the model · .xlsx or .csv" },
];

export const BOOK_LABEL: Record<Book, string> = {
  asset: "Asset Book",
  liability: "Liability Book",
  historical: "Historical Log",
};
