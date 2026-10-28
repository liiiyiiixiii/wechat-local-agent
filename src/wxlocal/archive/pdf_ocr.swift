import Foundation
import PDFKit
import AppKit
import Vision
let args=CommandLine.arguments
if args.count != 3 { exit(2) }
guard let doc=PDFDocument(url:URL(fileURLWithPath:args[1])) else { exit(3) }
var sections=[String]()
for index in 0..<doc.pageCount {
 try autoreleasepool {
  guard let page=doc.page(at:index) else { throw NSError(domain:"PDF",code:1) }
  let bounds=page.bounds(for:.mediaBox)
  let scale=min(2400/max(bounds.width,bounds.height),4)
  let bitmap=page.thumbnail(of:NSSize(width:bounds.width*scale,height:bounds.height*scale),for:.mediaBox)
  var rect=CGRect(origin:.zero,size:bitmap.size)
  guard let image=bitmap.cgImage(forProposedRect:&rect,context:nil,hints:nil) else { throw NSError(domain:"PDF",code:2) }
  let request=VNRecognizeTextRequest()
  request.recognitionLevel = .accurate
  request.recognitionLanguages=["zh-Hans","en-US"]
  request.usesLanguageCorrection=true
  try VNImageRequestHandler(cgImage:image,options:[:]).perform([request])
  let text=(request.results ?? []).compactMap{$0.topCandidates(1).first?.string}.joined(separator:"\n")
  sections.append("第 \(index+1) 页（本机 OCR，可能存在识别错误）\n"+text)
 }
}
try sections.joined(separator:"\n\n").write(toFile:args[2],atomically:true,encoding:.utf8)
print("OCR pages: \(doc.pageCount)")
