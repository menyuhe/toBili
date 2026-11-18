import type {Component} from 'vue'
import type {RouteRecordRaw} from 'vue-router'
interface Module { widgets?:Component[]; overviewActions?:Component[]; routes:RouteRecordRaw[]; nav:{to:string;label:string;icon:string;risk?:boolean}[] }
const installed=Object.values(import.meta.glob<Module>('./extensions/*/index.ts',{eager:true}))
export const extraRoutes=installed.flatMap(p=>p.routes)
export const extraNav=installed.flatMap(p=>p.nav)

export const extraWidgets=installed.flatMap(p=>p.widgets||[])
export const overviewActions=installed.flatMap(p=>p.overviewActions||[])
