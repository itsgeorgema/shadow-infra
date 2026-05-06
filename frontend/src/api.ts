import { createClient } from '@supabase/supabase-js'
import type { ResponsePair, ShadowDeployment, Verdict } from './types'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error(
    'Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY environment variables. ' +
      'Copy .env.example to .env and fill in your Supabase credentials.',
  )
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey)

/**
 * Fetch all shadow deployments, newest first.
 */
export async function listDeployments(): Promise<ShadowDeployment[]> {
  const { data, error } = await supabase
    .from('shadow_deployments')
    .select('*')
    .order('created_at', { ascending: false })

  if (error) throw new Error(`listDeployments: ${error.message}`)
  return (data ?? []) as ShadowDeployment[]
}

/**
 * Fetch all response pairs for a given deployment, newest first.
 */
export async function getPairsForDeployment(deploymentId: string): Promise<ResponsePair[]> {
  const { data, error } = await supabase
    .from('response_pairs')
    .select('*')
    .eq('deployment_id', deploymentId)
    .order('captured_at', { ascending: false })

  if (error) throw new Error(`getPairsForDeployment: ${error.message}`)
  return (data ?? []) as ResponsePair[]
}

/**
 * Fetch the verdict for a specific response pair.
 * Returns null if no verdict has been recorded yet.
 */
export async function getVerdictForPair(pairId: string): Promise<Verdict | null> {
  const { data, error } = await supabase
    .from('verdicts')
    .select('*')
    .eq('pair_id', pairId)
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle()

  if (error) throw new Error(`getVerdictForPair: ${error.message}`)
  return (data ?? null) as Verdict | null
}
