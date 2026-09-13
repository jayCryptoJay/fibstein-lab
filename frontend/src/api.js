export async function api(path,body){
  const response=await fetch('/api'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok){const detail=data.detail;throw new Error(Array.isArray(detail)?detail.map(x=>`${x.loc?.slice(1).join('.')}: ${x.msg}`).join('\n'):detail||'Request failed');}
  return data;
}
export function downloadJSON(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
export const fmt=(n,d=2)=>n===null||n===undefined?'—':Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
export const when=t=>new Date(t).toISOString().replace('T',' ').slice(0,16)+' UTC';
