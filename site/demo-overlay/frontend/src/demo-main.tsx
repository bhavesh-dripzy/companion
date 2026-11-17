import React,{useState,useCallback,useEffect} from 'react';
import {createRoot} from 'react-dom/client';
import {ProductShell} from './components/ProductShell';
import {PrototypeAssets} from './components/PrototypeAssets';
import {RisksPage} from './components/RisksPage';
import {OverviewPage} from './components/OverviewPage';
import {assets,findings} from './demo-data';
import type {AssetFilters,Pane} from './types';
import type {RiskFilters} from './hooks/useRisks';
import './styles.css';
import './exposure.css';
import './shell.css';
import '../../prototype/src/styles/tokens.css';
import '../../prototype/src/styles/base.css';
import '../../prototype/src/styles/components.css';
import '../../prototype/src/styles/netra.css';
import './prototype-assets.css';
import './asset-priorities.css';
import './risk-overviews.css';
import './overview.css';
import './demo-only.css';

const noop=()=>{};
function Demo(){
 const initial=new URLSearchParams(location.search).get('view');
 const[pane,setPane]=useState<Pane>(initial==='risks'?'findings':'exposure');
 const[af,setAf]=useState<AssetFilters>({query:'',reachability:'resolved',port:'all',vulnerable:false});
 const[rf,setRf]=useState<RiskFilters>({query:'',severity:'all',status:'all',priorityOnly:false,showTransportSecurity:false,sort:'severity',pageSize:25});
 const onLoad=useCallback(async(host:string)=>{const a=assets.find(a=>a.host===host)!;return {endpoints:a.endpoints,services:a.services,parameters:a.parameters,findings:a.findings};},[]);
 const rows=assets.filter(a=>(!af.query||a.host.includes(af.query.toLowerCase()))&&(!af.vulnerable||a.openRiskCount>0));
 const risks=findings.filter(f=>(!rf.query||`${f.title} ${f.host}`.toLowerCase().includes(rf.query.toLowerCase()))&&(rf.severity==='all'||f.severity===rf.severity)&&(rf.status==='all'||f.risk_status===rf.status)&&(!rf.priorityOnly||f.priority>=4)).sort((a,b)=>rf.sort==='host'?a.host.localeCompare(b.host):rf.sort==='oldest'?(a.first_seen_at??0)-(b.first_seen_at??0):rf.sort==='latest'?(b.first_seen_at??0)-(a.first_seen_at??0):b.priority-a.priority);
 const navigate=(next:Pane)=>{if(next==='findings'||next==='exposure'||next==='overview')setPane(next);};
 useEffect(()=>{
  const removeDashes=()=>{const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;while(n=walker.nextNode()){if(n.nodeValue?.includes(String.fromCharCode(8212)))n.nodeValue=n.nodeValue.replaceAll(String.fromCharCode(8212),'-');}};
  const observer=new MutationObserver(removeDashes);observer.observe(document.body,{childList:true,subtree:true});removeDashes();return()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  const blockDemoNavigation=(event:MouseEvent)=>{
   const anchor=(event.target as Element)?.closest('a');
   if(anchor&&new URL(anchor.href).hostname.endsWith('.example'))event.preventDefault();
  };
  document.addEventListener('click',blockDemoNavigation);
  return()=>document.removeEventListener('click',blockDemoNavigation);
 },[]);
 useEffect(()=>{
  const handler=(event:MessageEvent)=>{
   if(event.origin!==location.origin||event.data?.source!=='jatayu-landing')return;
   if(event.data.view==='assets'){setPane('exposure');document.querySelector<HTMLButtonElement>('[aria-label="Close risk details"]')?.click();}
   if(event.data.view==='risks')setPane('findings');
  };
  window.addEventListener('message',handler);return()=>window.removeEventListener('message',handler);
 },[]);
 return <><div className="fixture-banner"><strong>VAAYU PRODUCT DEMO</strong><span>Fictional data. No live testing. <a href="/">Back to Jatayu Labs ↗</a></span></div><ProductShell pane={pane} onPane={navigate} activeRuns={0} apiState="demo" onLogout={noop} onRefresh={noop} searchQuery="" onSearch={noop}>
 {pane==='overview'?<OverviewPage token="synthetic-preview-only" onAuthExpired={noop} onPane={navigate}/>:pane==='exposure'?<PrototypeAssets assets={rows} assetCursor={null} assetHasPrevious={false} assetOffset={0} assetTotal={rows.length} loading="Demo dataset" onLoadAssetDetail={onLoad} onNextAssets={noop} onPreviousAssets={noop} onUpdateAssetReview={async()=>{}} facets={{ports:[443]}} filters={af} onFilters={setAf} summary={{internet_facing:12,unique_ips:12,with_open_risks:4,accepted:12}}/>:<RisksPage clearFilters={()=>setRf({...rf,query:'',severity:'all',status:'all',priorityOnly:false})} filters={rf} findings={risks} hasNext={false} hasPrevious={false} loading="Demo dataset" nextPage={async()=>{}} offset={0} onAuthExpired={noop} previousPage={async()=>{}} scope="northstar.example" summary={{open:4,critical_high:3,assets_affected:4,new_this_week:4}} token="synthetic-preview-only" total={risks.length} updateFilters={p=>setRf({...rf,...p})}/>}
 </ProductShell></>;
}
createRoot(document.getElementById('root')!).render(<Demo/>);
