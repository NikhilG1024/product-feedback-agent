import { useEffect, useState } from 'react';
import { Package } from 'lucide-react';
import type { Product } from './types';
type Images = Record<string, {src:string; source_url?:string}>;
let manifest: Promise<Images> | undefined;
function images(): Promise<Images> {
 return manifest ??= fetch('/product-images.json').then(r => r.ok ? r.json() as Promise<Images> : {}).catch(() => ({}));
}
export function ProductImage({product}: {product:Product}) {
 const [src,setSrc]=useState<string>();
 const [failed,setFailed]=useState(false);
 useEffect(() => {
  let active=true; setSrc(undefined); setFailed(false);
  void images().then(map => {if(active) setSrc(map[product.id]?.src);});
  return () => {active=false;};
 },[product.id]);
 return <span className="product-photo">{src && !failed
  ? <img src={src} alt={product.title} loading="lazy" onError={()=>setFailed(true)} />
  : <span className="product-photo-fallback"><Package size={28}/><small>Photo unavailable</small></span>}</span>;
}
