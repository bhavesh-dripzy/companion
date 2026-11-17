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
type Scenario = {
 description: string; impact: string; remediation: string; steps: string[];
 requestHeadline: string; requestHeaders: string[]; requestFields: {label:string;value:string}[];
 responseHeadline: string; responseHeaders: string[]; responseFields: {label:string;value:string}[];
 body: Record<string, unknown>;
};
const scenarios: Record<string, Scenario> = {
 'demo-invoice-access': {
  description:'Synthetic demonstration: a signed-in user in Workspace A can request an invoice assigned to Workspace B. The endpoint checks that the session is valid, but does not enforce ownership of the requested invoice.',
  impact:'In this fictional example, a customer could access another workspace\'s invoice details, including billing information. All identities, records, and traffic in this demo are synthetic.',
  remediation:'Enforce object-level authorization on every invoice request.',
  steps:['Resolve the workspace from the authenticated session, not a client-controlled identifier.','Verify that the requested invoice belongs to the authorized workspace.','Add regression tests for same-workspace and cross-workspace access.'],
  requestHeadline:'Synthetic request from Workspace A', requestHeaders:['Authorization: Bearer [SYNTHETIC_SESSION_A]'],
  requestFields:[{label:'Workspace',value:'Workspace A (demo)'},{label:'Requested object',value:'Invoice owned by Workspace B (demo)'}],
  responseHeadline:'Synthetic response includes a different workspace\'s invoice', responseHeaders:[],
  responseFields:[{label:'Object owner',value:'Workspace B (demo)'},{label:'Authorization boundary',value:'Not enforced in this fictional scenario'}],
  body:{invoice_id:'DEMO-INV-204',workspace:'Workspace B (demo)',customer:'Fictional customer',amount:'Synthetic value'}
 },
 'demo-export': {
  description:'Synthetic demonstration: a customer export endpoint returns a fictional record without an authenticated session.',
  impact:'In this fictional scenario, an unauthenticated visitor could retrieve customer information. No real customer records or live requests are used.',
  remediation:'Require authentication and explicit export authorization.',
  steps:['Require a valid authenticated session before generating or serving exports.','Scope exported records to the authorized organization.','Test unauthenticated and unauthorized export requests.'],
  requestHeadline:'Synthetic request without authentication', requestHeaders:[],
  requestFields:[{label:'Authentication',value:'None (fictional request)'},{label:'Requested object',value:'Demo customer export'}],
  responseHeadline:'Synthetic response returns a customer export', responseHeaders:[],
  responseFields:[{label:'Returned record',value:'Fictional customer'},{label:'Authentication boundary',value:'Not enforced in this fictional scenario'}],
  body:{demo:true,customers:[{name:'Fictional customer',email:'demo@northstar.example'}]}
 },
 'demo-tenant': {
  description:'Synthetic demonstration: a session associated with Workspace A can read the fictional membership list of Workspace B.',
  impact:'This fictional example exposes another workspace\'s membership information. All accounts and records are synthetic.',
  remediation:'Enforce workspace membership before returning workspace details.',
  steps:['Derive the authorized workspace from the current session.','Check membership and permissions on each workspace request.','Add cross-workspace access-control regression tests.'],
  requestHeadline:'Synthetic session from Workspace A', requestHeaders:['Authorization: Bearer [SYNTHETIC_SESSION_A]'],
  requestFields:[{label:'Session workspace',value:'Workspace A (demo)'},{label:'Requested workspace',value:'Workspace B (demo)'}],
  responseHeadline:'Synthetic response reveals another workspace\'s members', responseHeaders:[],
  responseFields:[{label:'Returned workspace',value:'Workspace B (demo)'},{label:'Membership boundary',value:'Not enforced in this fictional scenario'}],
  body:{demo:true,workspace:'Workspace B (demo)',members:[{name:'Fictional member',role:'Viewer'}]}
 },
 'demo-cors': {
  description:'Synthetic demonstration: a profile endpoint permits a fictional untrusted origin to read a credentialed response.',
  impact:'In this fictional scenario, account information could be read by a page on an untrusted origin. No browser attack or live request is performed.',
  remediation:'Restrict credentialed cross-origin access to explicitly trusted origins.',
  steps:['Use an explicit allowlist of trusted origins.','Do not reflect arbitrary Origin headers.','Test both trusted and untrusted credentialed requests.'],
  requestHeadline:'Synthetic request with an untrusted Origin',
  requestHeaders:['Origin: https://untrusted.example','Cookie: demo_session=[SYNTHETIC]'],
  requestFields:[{label:'Origin',value:'https://untrusted.example (fictional)'},{label:'Credentials',value:'Synthetic demo session'}],
  responseHeadline:'Synthetic response allows the untrusted origin',
  responseHeaders:['Access-Control-Allow-Origin: https://untrusted.example','Access-Control-Allow-Credentials: true'],
  responseFields:[{label:'Allowed origin',value:'https://untrusted.example (fictional)'},{label:'Credentialed access',value:'Allowed in this fictional scenario'}],
  body:{demo:true,account:'Fictional customer',workspace:'Workspace A (demo)'}
 }
};
export function overview(id: string): RiskOverview {
 const f=findings.find(f=>f.finding_id===id)??findings[0];
 const scenario=scenarios[f.finding_id];
 return {finding_id:id,template_id:'demo-'+f.finding_id,title:f.title!,status:'ready',revision:1,
 vulnerability_description:scenario.description, impact:scenario.impact,
 remediation:{summary:scenario.remediation,steps:scenario.steps},
 references:[{title:'OWASP API Security',url:'https://owasp.org/API-Security/',publisher:'OWASP'}]};
}
export function evidence(id:string):RiskEvidenceResponse {
 const f=findings.find(f=>f.finding_id===id)??findings[0];
 const target=f.url;
 const scenario=scenarios[f.finding_id];
 const requestRaw=[`GET ${new URL(target).pathname} HTTP/1.1`,`Host: ${f.host}`,...scenario.requestHeaders,'X-Demo-Data: true'].join('\n');
 const responseRaw=['HTTP/1.1 200 OK','Content-Type: application/json',...scenario.responseHeaders,'X-Demo-Data: true','',JSON.stringify(scenario.body,null,2)].join('\n');
 return {finding_id:id,impacts:[{impact_id:'demo-impact',asset:f.host,locator:target,first_seen_at:stamp,last_seen_at:stamp,evidence_count:1}],evidence:[{evidence_id:'demo-evidence',impact_id:'demo-impact',capability_id:'demo-only',contract_version:1,evidence_type:'synthetic-http-exchange',observed_at:stamp,status:'ready',observation:{type:'exchange',protocol:'http',target:{asset_reference:target}},blocks:[{type:'exchange_chain',exchanges:[{sequence:1,role:'primary',protocol:'https',target,observed_at:stamp,
 request:{raw:requestRaw,available:true,truncated:false,summary:{headline:scenario.requestHeadline,fields:scenario.requestFields}},
 response:{raw:responseRaw,available:true,truncated:false,summary:{headline:scenario.responseHeadline,status_code:200,fields:scenario.responseFields}}
 }]}]}]};
}
