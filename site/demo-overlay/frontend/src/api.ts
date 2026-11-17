// Marketing preview only. Replaces the network layer in this isolated copy.
// No authentication, production API, testing, or external requests are used.
import { overview, evidence, stamp, findings, assets, now } from './demo-data';
export class ApiError extends Error { constructor(public status:number){super(`Demo API ${status}`);} }
export function isAuthExpired(error:unknown){return error instanceof ApiError&&error.status===401;}
export function isAuthoritativeProjectionUnavailable(error:unknown){return error instanceof ApiError&&error.status===503;}
const demoNotes:Record<string,Array<{comment_id:string;actor_type:'user';actor_id:string;display_name:string;body:string;created_at:string}>>={};
export async function apiJson<T>(path:string,_init?:RequestInit,_token?:string|null):Promise<T>{
 if(path.startsWith('/overview?'))return {period_days:30,risk_distribution:{critical:1,high:2,medium:1},risk_trend:[{date:'2026-11-13',value:0},{date:'2026-11-14',value:1},{date:'2026-11-15',value:2},{date:'2026-11-16',value:3},{date:'2026-11-17',value:4}],asset_trend:[{date:'2026-11-13',value:3},{date:'2026-11-14',value:5},{date:'2026-11-15',value:8},{date:'2026-11-16',value:10},{date:'2026-11-17',value:12}],top_risks:findings.map(f=>({title:f.title,host:f.host,severity:f.severity,type:'Access control',last_seen_at:now})),recent_assets:assets.slice(0,5).map(a=>({host:a.host,asset_type:'dns',open_risks:a.openRiskCount,first_seen_at:now,last_seen_at:now})),map:{status:'location_data_unavailable',points:[]}} as T;
 if(path.startsWith('/assets/priorities'))return {items:assets.slice(0,3).map((a,i)=>({asset_id:a.entityId,host:a.host,seed_domain:'northstar.example',account_rank:i+1,review_status:'accepted',scan_enabled:true,last_seen_at:now,active_service_count:1,ports:[443],reasons:[{label:'Customer-facing application',detail:'Fictional demo asset used in this walkthrough.',basis:'observed'}],interesting_endpoints:[{method:'GET',path:'/',reasons:['Synthetic demonstration']}],rubric:{checked:1,matched:1,not_matched:0,unavailable:0}})),total_eligible:12} as T;
 const match=path.match(/^\/findings\/([^/]+)\/(overview|evidence|comments)$/);
 if(!match)throw new Error(`No demo fixture for ${path}`);
 const [,id,kind]=match;
 if(kind==='overview')return overview(id) as T;
 if(kind==='evidence')return evidence(id) as T;
 const notes=demoNotes[id]??(demoNotes[id]=[{comment_id:'demo-comment',actor_type:'user',actor_id:'demo-reviewer',display_name:'Demo reviewer',body:'Synthetic example for the product walkthrough. Review the affected workspace boundary with the API team.',created_at:stamp}]);
 if(_init?.method==='POST'){const value=JSON.parse(String(_init.body??'{}'));notes.push({comment_id:`local-${notes.length}`,actor_type:'user',actor_id:'demo-reviewer',display_name:'Demo reviewer',body:String(value.body??'').slice(0,4000),created_at:stamp});}
 return {finding_id:id,comments:notes.slice()} as T;
}
