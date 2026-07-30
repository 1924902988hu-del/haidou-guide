#!/usr/bin/env swift
import AppKit
import Foundation
import Vision

struct TextObservation: Codable {
    let text: String
    let confidence: Float
    let bbox: [Double]
}

struct RectangleObservation: Codable {
    let confidence: Float
    let bbox: [Double]
}

struct FrameResult: Codable {
    let path: String
    let texts: [TextObservation]
    let rectangles: [RectangleObservation]
    let error: String?
}

func cgImage(at path: String) -> CGImage? {
    guard let image = NSImage(contentsOfFile: path) else { return nil }
    var rect = CGRect(origin: .zero, size: image.size)
    return image.cgImage(forProposedRect: &rect, context: nil, hints: nil)
}

let input = FileHandle.standardInput.readDataToEndOfFile()
guard let paths = try? JSONDecoder().decode([String].self, from: input) else {
    FileHandle.standardError.write(Data("invalid JSON input\n".utf8))
    exit(2)
}

var results: [FrameResult] = []
for path in paths {
    guard let image = cgImage(at: path) else {
        results.append(FrameResult(path: path, texts: [], rectangles: [], error: "image-decode-failed"))
        continue
    }

    let textRequest = VNRecognizeTextRequest()
    textRequest.recognitionLevel = .accurate
    textRequest.usesLanguageCorrection = true
    textRequest.recognitionLanguages = ["zh-Hans", "en-US"]
    textRequest.minimumTextHeight = 0.012

    let rectangleRequest = VNDetectRectanglesRequest()
    rectangleRequest.maximumObservations = 120
    rectangleRequest.minimumConfidence = 0.45
    rectangleRequest.minimumAspectRatio = 0.72
    rectangleRequest.maximumAspectRatio = 1.0
    rectangleRequest.minimumSize = 0.018
    rectangleRequest.quadratureTolerance = 25

    do {
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([textRequest, rectangleRequest])
        let texts = (textRequest.results ?? []).compactMap { observation -> TextObservation? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            return TextObservation(
                text: candidate.string,
                confidence: candidate.confidence,
                bbox: [box.origin.x, box.origin.y, box.size.width, box.size.height]
            )
        }
        let rectangles = (rectangleRequest.results ?? []).map { observation in
            let box = observation.boundingBox
            return RectangleObservation(
                confidence: observation.confidence,
                bbox: [box.origin.x, box.origin.y, box.size.width, box.size.height]
            )
        }
        results.append(FrameResult(path: path, texts: texts, rectangles: rectangles, error: nil))
    } catch {
        results.append(FrameResult(path: path, texts: [], rectangles: [], error: String(describing: error)))
    }
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let output = try encoder.encode(results)
FileHandle.standardOutput.write(output)
