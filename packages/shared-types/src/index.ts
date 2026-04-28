export interface ApiResponse<T> {
  data: T;
  message?: string;
}

export interface PaginatedResponse<T> extends ApiResponse<T[]> {
  total: number;
  page: number;
  pageSize: number;
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "teacher" | "student";
  createdAt: string;
}
