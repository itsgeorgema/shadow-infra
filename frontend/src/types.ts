export type DeploymentStatus = 'active' | 'closed' | 'error';
export type VerdictType = 'Safe' | 'Warning' | 'Critical';

export interface ShadowDeployment {
  id: string;
  pr_number: number;
  pr_title: string;
  repo: string;
  branch: string;
  shadow_url: string;
  status: DeploymentStatus;
  created_at: string;
  updated_at: string;
}

export interface ResponsePair {
  id: string;
  deployment_id: string;
  request_path: string;
  request_method: string;
  prod_status: number;
  prod_headers: Record<string, string>;
  prod_body: string;
  shadow_status: number;
  shadow_headers: Record<string, string>;
  shadow_body: string;
  captured_at: string;
}

export interface Verdict {
  id: string;
  pair_id: string;
  verdict: VerdictType;
  reasoning: string;
  diff_summary: string;
  created_at: string;
}
