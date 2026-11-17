import type { LiveFinding, RiskOverview, RiskEvidenceResponse } from './types';
import type { ExposureAsset } from './exposureModel';

export const now = Date.parse('2026-11-17T09:30:00Z');
export const stamp = '2026-11-17T09:30:00Z';
export const findings: LiveFinding[] = [
  {finding_id:'demo-invoice-access',risk_id:'demo-invoice-access',title:'Cross-account access to customer invoices',severity:'high',priority:4,risk_status:'open',host:'api.northstar.example',target_domain:'northstar.example',url:'https://api.northstar.example/invoices/example',port:443,status:200,source:'vulnerability-scanning',technologies:['Node.js','REST API'],surface_hints:['access_control'],first_seen_at:now-86400000,last_seen_at:now},
  {finding_id:'demo-export',risk_id:'demo-export',title:'Unauthenticated customer export',severity:'critical',priority:5,risk_status:'open',host:'exports.northstar.example',target_domain:'northstar.example',url:'https://exports.northstar.example/exports/demo',port:443,status:200,source:'vulnerability-scanning',technologies:['nginx'],surface_hints:['sensitive_data_exposure'],first_seen_at:now-86400000,last_seen_at:now},
  {finding_id:'demo-tenant',risk_id:'demo-tenant',title:'Workspace membership boundary bypass',severity:'high',priority:4,risk_status:'triage',host:'app.northstar.example',target_domain:'northstar.example',url:'https://app.northstar.example/workspaces/demo',port:443,status:200,source:'vulnerability-scanning',technologies:['React','Node.js'],surface_hints:['authorization'],first_seen_at:now-172800000,last_seen_at:now},
  {finding_id:'demo-cors',risk_id:'demo-cors',title:'Cross-origin access to account information',severity:'medium',priority:3,risk_status:'open',host:'accounts.northstar.example',target_domain:'northstar.example',url:'https://accounts.northstar.example/profile',port:443,status:200,source:'vulnerability-scanning',technologies:['REST API'],surface_hints:['cross_origin'],first_seen_at:now-86400000,last_seen_at:now},
];
const hosts=['exports','api','app','accounts','www','status','docs','staging','media','support','payments','auth'];
export const assets: ExposureAsset[] = hosts.map((sub,i) => {
 const host=`${sub}.northstar.example`;
 const risks=findings.filter(f=>f.host===host);
 const max=Math.max(0,...risks.map(f=>f.priority));
 return {
  entityId:`demo-asset-${i}`,assetType:'dns',host,ips:[`203.0.113.${10+i}`],ports:[443],technologies:i===1?['Node.js','REST API']:i===2?['React']:['nginx'],
  endpoints:[{endpoint_id:`demo-endpoint-${i}`,host,url:`https://${host}/`,path:'/',method:'GET',scheme:'https',status:200,port:443,last_seen_at:now,technologies:i===1?['Node.js']:['nginx']}],
  parameters:[],services:[{service_id:`demo-service-${i}`,host,ip:`203.0.113.${10+i}`,port:443,transport:'tcp',state:'open',name:'https',product:'nginx',identification_status:'identified',technologies:['HTTPS'],last_seen_at:now}],findings:risks,
  openRiskCount:risks.length,highestRiskSeverity:risks[0]?.severity??null,highestRiskCount:risks.length,priority:max,reviewStatus:'accepted',accepted:true,scanEnabled:true,firstSeenAt:now-604800000,lastSeenAt:now,
 };
});
export function overview(id: string): RiskOverview {
 const f=findings.find(f=>f.finding_id===id)??findings[0];
 return {finding_id:id,template_id:'demo-access-control',title:f.title!,status:'ready',revision:1,
 vulnerability_description:'Synthetic demonstration: a signed-in user in Workspace A can request an invoice assigned to Workspace B. The endpoint checks that the session is valid, but does not enforce ownership of the requested invoice.',
 impact:'In this fictional example, a customer could access another workspace\'s invoice details, including billing information. All identities, records, and traffic in this demo are synthetic.',
 remediation:{summary:'Enforce object-level authorization on every invoice request.',steps:['Resolve the workspace from the authenticated session, not a client-controlled identifier.','Verify that the requested invoice belongs to the authorized workspace.','Add regression tests for same-workspace and cross-workspace access.']},
 references:[{title:'API1:2023 Broken Object Level Authorization',url:'https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/',publisher:'OWASP'}]};
}
export function evidence(id:string):RiskEvidenceResponse {
 const f=findings.find(f=>f.finding_id===id)??findings[0];
 const target=f.url;
 return {finding_id:id,impacts:[{impact_id:'demo-impact',asset:f.host,locator:target,first_seen_at:stamp,last_seen_at:stamp,evidence_count:1}],evidence:[{evidence_id:'demo-evidence',impact_id:'demo-impact',capability_id:'demo-only',contract_version:1,evidence_type:'synthetic-http-exchange',observed_at:stamp,status:'ready',observation:{type:'exchange',protocol:'http',target:{asset_reference:target}},blocks:[{type:'exchange_chain',exchanges:[{sequence:1,role:'primary',protocol:'https',target,observed_at:stamp,
 request:{raw:`GET /invoices/example HTTP/1.1\nHost: ${f.host}\nAuthorization: Bearer [SYNTHETIC_SESSION_A]\nX-Demo-Data: true`,available:true,truncated:false,summary:{headline:'Synthetic request from Workspace A',fields:[{label:'Workspace',value:'Workspace A (demo)'},{label:'Requested object',value:'Invoice owned by Workspace B (demo)'}]}},
 response:{raw:'HTTP/1.1 200 OK\nContent-Type: application/json\nX-Demo-Data: true\n\n{\n  "invoice_id": "DEMO-INV-204",\n  "workspace": "Workspace B (demo)",\n  "customer": "Fictional customer",\n  "amount": "Synthetic value"\n}',available:true,truncated:false,summary:{headline:'Synthetic response includes a different workspace\'s invoice',status_code:200,fields:[{label:'Object owner',value:'Workspace B (demo)'},{label:'Authorization boundary',value:'Not enforced in this fictional scenario'}]}}
 }]}]}]};
}
