/** qrcode 没有自带类型，官方类型包不在依赖里。
 *  只用到 toCanvas 一个方法，声明这样一个最小接口比多装一个 @types 包更省事。
 */
declare module 'qrcode' {
  interface RenderOptions {
    width?: number
    margin?: number
    errorCorrectionLevel?: 'low' | 'medium' | 'quartile' | 'high'
    color?: { dark?: string; light?: string }
  }
  const QRCode: {
    toCanvas(canvas: HTMLCanvasElement, text: string, options?: RenderOptions): Promise<void>
  }
  export default QRCode
}
