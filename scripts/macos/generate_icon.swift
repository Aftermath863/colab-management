#!/usr/bin/env swift
//
// generate_icon.swift
// Programmatically renders a 1024x1024 Apple Silicon Obsidian & Liquid Glass
// macOS Squircle iconset and compiles it into an .icns bundle via iconutil.
//
// Usage:
//   swift scripts/macos/generate_icon.swift <output_icns_path>
//

import AppKit
import CoreGraphics
import Foundation

struct IconSpec {
    let filename: String
    let pixelSize: Int
}

private let iconSpecs: [IconSpec] = [
    IconSpec(filename: "icon_16x16.png", pixelSize: 16),
    IconSpec(filename: "icon_16x16@2x.png", pixelSize: 32),
    IconSpec(filename: "icon_32x32.png", pixelSize: 32),
    IconSpec(filename: "icon_32x32@2x.png", pixelSize: 64),
    IconSpec(filename: "icon_128x128.png", pixelSize: 128),
    IconSpec(filename: "icon_128x128@2x.png", pixelSize: 256),
    IconSpec(filename: "icon_256x256.png", pixelSize: 256),
    IconSpec(filename: "icon_256x256@2x.png", pixelSize: 512),
    IconSpec(filename: "icon_512x512.png", pixelSize: 512),
    IconSpec(filename: "icon_512x512@2x.png", pixelSize: 1024),
]

private let srgb = CGColorSpace(name: CGColorSpace.sRGB)!

private func rgba(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1.0) -> CGColor {
    CGColor(colorSpace: srgb, components: [r / 255.0, g / 255.0, b / 255.0, a])!
}

private func hexColor(_ hex: UInt32, alpha: CGFloat = 1.0) -> CGColor {
    let r = CGFloat((hex >> 16) & 0xFF)
    let g = CGFloat((hex >> 8) & 0xFF)
    let b = CGFloat(hex & 0xFF)
    return rgba(r, g, b, alpha)
}

/// Builds a continuous Apple-style squircle (superellipse) path inside `rect`.
private func createSquirclePath(in rect: CGRect, exponent: CGFloat = 4.6, segments: Int = 360) -> CGPath {
    let path = CGMutablePath()
    let cx = rect.midX
    let cy = rect.midY
    let a = rect.width * 0.5
    let b = rect.height * 0.5
    let p = 2.0 / exponent

    for i in 0..<segments {
        let theta = (CGFloat(i) / CGFloat(segments)) * (.pi * 2.0)
        let cosT = cos(theta)
        let sinT = sin(theta)
        let x = cx + a * copysign(pow(abs(cosT), p), cosT)
        let y = cy + b * copysign(pow(abs(sinT), p), sinT)
        if i == 0 {
            path.move(to: CGPoint(x: x, y: y))
        } else {
            path.addLine(to: CGPoint(x: x, y: y))
        }
    }
    path.closeSubpath()
    return path
}

/// Evaluates the parametric tensor infinity curve and its derivative `(dx/dt, dy/dt)`.
private func infinityPointAndTangent(
    t: CGFloat,
    center: CGPoint,
    rx: CGFloat,
    ry: CGFloat
) -> (point: CGPoint, tangent: CGPoint) {
    let sinT = sin(t)
    let cosT = cos(t)
    let k: CGFloat = 0.55
    let denom = 1.0 + k * sinT * sinT
    let dDenom = 2.0 * k * sinT * cosT

    let numX = rx * cosT
    let dNumX = -rx * sinT
    let numY = ry * sinT * cosT
    let dNumY = ry * (cosT * cosT - sinT * sinT)

    let x = center.x + numX / denom
    let y = center.y + numY / denom
    let dx = (dNumX * denom - numX * dDenom) / (denom * denom)
    let dy = (dNumY * denom - numY * dDenom) / (denom * denom)

    return (CGPoint(x: x, y: y), CGPoint(x: dx, y: dy))
}

/// Builds a C1-continuous cubic Bezier path for the infinity curve over `[tStart, tEnd]`.
private func createSmoothInfinityPath(
    center: CGPoint,
    rx: CGFloat,
    ry: CGFloat,
    tStart: CGFloat,
    tEnd: CGFloat,
    segments: Int,
    closed: Bool
) -> CGPath {
    let path = CGMutablePath()
    let dt = (tEnd - tStart) / CGFloat(segments)

    let first = infinityPointAndTangent(t: tStart, center: center, rx: rx, ry: ry)
    path.move(to: first.point)

    var prev = first
    for i in 1...segments {
        let t = tStart + dt * CGFloat(i)
        let curr = infinityPointAndTangent(t: t, center: center, rx: rx, ry: ry)
        let cp1 = CGPoint(
            x: prev.point.x + prev.tangent.x * (dt / 3.0),
            y: prev.point.y + prev.tangent.y * (dt / 3.0)
        )
        let cp2 = CGPoint(
            x: curr.point.x - curr.tangent.x * (dt / 3.0),
            y: curr.point.y - curr.tangent.y * (dt / 3.0)
        )
        path.addCurve(to: curr.point, control1: cp1, control2: cp2)
        prev = curr
    }

    if closed {
        path.closeSubpath()
    }
    return path
}

private func drawRadialGlow(
    in ctx: CGContext,
    center: CGPoint,
    radius: CGFloat,
    innerColor: CGColor,
    outerColor: CGColor
) {
    guard let grad = CGGradient(
        colorsSpace: srgb,
        colors: [innerColor, outerColor] as CFArray,
        locations: [0.0, 1.0]
    ) else { return }
    ctx.drawRadialGradient(
        grad,
        startCenter: center,
        startRadius: 0,
        endCenter: center,
        endRadius: radius,
        options: [.drawsAfterEndLocation]
    )
}

private func makeRibbonGradient() -> CGGradient? {
    CGGradient(
        colorsSpace: srgb,
        colors: [
            hexColor(0x6366F1), // Deep Indigo
            hexColor(0x38BDF8), // Electric Sky / Cyan-Blue
            hexColor(0x818CF8), // Luminous Violet-Indigo bridge
            hexColor(0xF59E0B), // Colab Amber
            hexColor(0xFBBF24), // Warm Tensor Gold
        ] as CFArray,
        locations: [0.0, 0.28, 0.50, 0.74, 1.0]
    )
}

private func paintRibbonSegment(
    in ctx: CGContext,
    path: CGPath,
    width: CGFloat,
    lineCap: CGLineCap,
    highlightAlpha: CGFloat,
    drawHighlight: Bool
) {
    guard let ribbonGrad = makeRibbonGradient() else { return }

    ctx.saveGState()
    ctx.addPath(path)
    ctx.setLineWidth(width)
    ctx.setLineCap(lineCap)
    ctx.setLineJoin(.round)
    ctx.replacePathWithStrokedPath()
    ctx.clip()

    ctx.drawLinearGradient(
        ribbonGrad,
        start: CGPoint(x: 210, y: 580),
        end: CGPoint(x: 814, y: 444),
        options: [.drawsBeforeStartLocation, .drawsAfterEndLocation]
    )

    if drawHighlight {
        ctx.addPath(path)
        ctx.setLineWidth(width * 0.28)
        ctx.setLineCap(lineCap)
        ctx.setLineJoin(.round)
        ctx.setStrokeColor(rgba(255, 255, 255, highlightAlpha))
        ctx.strokePath()
    }
    ctx.restoreGState()
}

private func drawIcon(pixelSize: Int) -> CGImage? {
    guard let ctx = CGContext(
        data: nil,
        width: pixelSize,
        height: pixelSize,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: srgb,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        return nil
    }

    ctx.setAllowsAntialiasing(true)
    ctx.setShouldAntialias(true)
    ctx.interpolationQuality = .high

    // Scale virtual 1024x1024 canvas to exact pixel dimensions
    let scale = CGFloat(pixelSize) / 1024.0
    ctx.scaleBy(x: scale, y: scale)

    let tileRect = CGRect(x: 96, y: 96, width: 832, height: 832)
    let squirclePath = createSquirclePath(in: tileRect)
    let isTiny = pixelSize <= 32
    let isSmall = pixelSize <= 64

    // 1. Ambient outer shadow beneath the squircle tile
    ctx.saveGState()
    ctx.setShadow(
        offset: CGSize(width: 0, height: -18),
        blur: 38,
        color: rgba(2, 6, 23, 0.62)
    )
    ctx.addPath(squirclePath)
    ctx.setFillColor(hexColor(0x0B1120))
    ctx.fillPath()
    ctx.restoreGState()

    // 2. Clipped interior of the Obsidian & Liquid Glass tile
    ctx.saveGState()
    ctx.addPath(squirclePath)
    ctx.clip()

    // Base rich obsidian gradient (#0B1120 -> #111C33)
    if let baseGrad = CGGradient(
        colorsSpace: srgb,
        colors: [
            hexColor(0x0B1120),
            hexColor(0x0E172C),
            hexColor(0x111C33),
        ] as CFArray,
        locations: [0.0, 0.55, 1.0]
    ) {
        ctx.drawLinearGradient(
            baseGrad,
            start: CGPoint(x: 512, y: tileRect.maxY),
            end: CGPoint(x: 512, y: tileRect.minY),
            options: []
        )
    }

    // Atmospheric radial depth glows (Indigo top-left + Cyan mid-left + Colab Amber bottom-right)
    drawRadialGlow(
        in: ctx,
        center: CGPoint(x: 290, y: 720),
        radius: 520,
        innerColor: hexColor(0x4F46E5, alpha: 0.28),
        outerColor: hexColor(0x4F46E5, alpha: 0.0)
    )
    drawRadialGlow(
        in: ctx,
        center: CGPoint(x: 340, y: 500),
        radius: 400,
        innerColor: hexColor(0x0EA5E9, alpha: 0.14),
        outerColor: hexColor(0x0EA5E9, alpha: 0.0)
    )
    drawRadialGlow(
        in: ctx,
        center: CGPoint(x: 735, y: 310),
        radius: 480,
        innerColor: hexColor(0xF59E0B, alpha: 0.22),
        outerColor: hexColor(0xF59E0B, alpha: 0.0)
    )

    // 3. Subtle neural tensor grid & Apple Silicon GPU die accents (omitted at 16/32px to keep crisp)
    if !isTiny {
        ctx.saveGState()
        let gridStep: CGFloat = 64.0
        let gridMin: CGFloat = 128.0
        let gridMax: CGFloat = 896.0
        ctx.setLineWidth(isSmall ? 2.5 : 1.35)

        var x = gridMin
        while x <= gridMax {
            let distFromCenter = abs(x - 512.0) / 400.0
            let alpha = max(0.015, 0.075 * (1.0 - distFromCenter * 0.7))
            ctx.setStrokeColor(rgba(148, 163, 184, alpha))
            ctx.move(to: CGPoint(x: x, y: gridMin))
            ctx.addLine(to: CGPoint(x: x, y: gridMax))
            ctx.strokePath()
            x += gridStep
        }

        var y = gridMin
        while y <= gridMax {
            let distFromCenter = abs(y - 512.0) / 400.0
            let alpha = max(0.015, 0.075 * (1.0 - distFromCenter * 0.7))
            ctx.setStrokeColor(rgba(148, 163, 184, alpha))
            ctx.move(to: CGPoint(x: gridMin, y: y))
            ctx.addLine(to: CGPoint(x: gridMax, y: y))
            ctx.strokePath()
            y += gridStep
        }

        if !isSmall {
            let nodes: [(CGFloat, CGFloat, CGColor)] = [
                (256, 704, hexColor(0x6366F1, alpha: 0.34)),
                (384, 704, hexColor(0x38BDF8, alpha: 0.28)),
                (640, 704, hexColor(0x818CF8, alpha: 0.24)),
                (256, 320, hexColor(0x38BDF8, alpha: 0.24)),
                (640, 320, hexColor(0xF59E0B, alpha: 0.34)),
                (768, 320, hexColor(0xFBBF24, alpha: 0.28)),
                (320, 512, hexColor(0x38BDF8, alpha: 0.26)),
                (704, 512, hexColor(0xF59E0B, alpha: 0.26)),
            ]
            for (nx, ny, color) in nodes {
                ctx.setFillColor(color)
                ctx.fillEllipse(in: CGRect(x: nx - 3.5, y: ny - 3.5, width: 7, height: 7))
            }
        }
        ctx.restoreGState()
    }

    // 4. Inner GPU Socket Glass Housing
    let socketRect = CGRect(x: 242, y: 242, width: 540, height: 540)
    let socketPath = createSquirclePath(in: socketRect, exponent: 4.4, segments: 240)
    ctx.saveGState()
    ctx.addPath(socketPath)
    ctx.setFillColor(rgba(8, 13, 26, 0.56))
    ctx.fillPath()

    ctx.addPath(socketPath)
    ctx.setLineWidth(isTiny ? 4.0 : 2.0)
    ctx.setStrokeColor(rgba(148, 163, 184, 0.16))
    ctx.strokePath()

    // Silicon package pin ticks around the GPU socket
    if !isSmall {
        ctx.setLineCap(.round)
        ctx.setLineWidth(3.5)
        let pinOffsets: [CGFloat] = [-168, -120, -72, -24, 24, 72, 120, 168]
        for offset in pinOffsets {
            let isAmberSide = offset > 0
            let pinColor = isAmberSide ? hexColor(0xF59E0B, alpha: 0.28) : hexColor(0x6366F1, alpha: 0.32)
            ctx.setStrokeColor(pinColor)

            ctx.move(to: CGPoint(x: 512 + offset, y: socketRect.maxY + 6))
            ctx.addLine(to: CGPoint(x: 512 + offset, y: socketRect.maxY + 24))
            ctx.strokePath()

            ctx.move(to: CGPoint(x: 512 + offset, y: socketRect.minY - 6))
            ctx.addLine(to: CGPoint(x: 512 + offset, y: socketRect.minY - 24))
            ctx.strokePath()

            ctx.setStrokeColor(hexColor(0x38BDF8, alpha: 0.28))
            ctx.move(to: CGPoint(x: socketRect.minX - 6, y: 512 + offset))
            ctx.addLine(to: CGPoint(x: socketRect.minX - 24, y: 512 + offset))
            ctx.strokePath()

            ctx.setStrokeColor(hexColor(0xFBBF24, alpha: 0.28))
            ctx.move(to: CGPoint(x: socketRect.maxX + 6, y: 512 + offset))
            ctx.addLine(to: CGPoint(x: socketRect.maxX + 24, y: 512 + offset))
            ctx.strokePath()
        }
    }
    ctx.restoreGState()

    // 5. Specular Top Sheen across upper glass surface
    ctx.saveGState()
    let sheenRect = CGRect(x: 60, y: 540, width: 904, height: 470)
    let sheenPath = CGPath(ellipseIn: sheenRect, transform: nil)
    ctx.addPath(sheenPath)
    ctx.clip()
    if let sheenGrad = CGGradient(
        colorsSpace: srgb,
        colors: [
            rgba(255, 255, 255, 0.14),
            rgba(226, 232, 240, 0.04),
            rgba(255, 255, 255, 0.0),
        ] as CFArray,
        locations: [0.0, 0.55, 1.0]
    ) {
        ctx.drawLinearGradient(
            sheenGrad,
            start: CGPoint(x: 512, y: tileRect.maxY),
            end: CGPoint(x: 512, y: 540),
            options: []
        )
    }
    ctx.restoreGState()

    // 6. Glowing Indigo/Blue-to-Colab-Amber Tensor Infinity / GPU Core Emblem
    let center = CGPoint(x: 512, y: 512)
    let rx: CGFloat = 296
    let ry: CGFloat = 264
    let ribbonWidth: CGFloat = isTiny ? 80 : (isSmall ? 68 : 60)

    // Back-glow halos under the two tensor loops
    drawRadialGlow(
        in: ctx,
        center: CGPoint(x: 372, y: 512),
        radius: 215,
        innerColor: hexColor(0x6366F1, alpha: 0.42),
        outerColor: hexColor(0x6366F1, alpha: 0.0)
    )
    drawRadialGlow(
        in: ctx,
        center: CGPoint(x: 652, y: 512),
        radius: 215,
        innerColor: hexColor(0xF59E0B, alpha: 0.38),
        outerColor: hexColor(0xF59E0B, alpha: 0.0)
    )

    // Smooth 16-segment cubic Bezier infinity path (zero polyline tessellation seams)
    let fullInfinityPath = createSmoothInfinityPath(
        center: center,
        rx: rx,
        ry: ry,
        tStart: 0,
        tEnd: .pi * 2.0,
        segments: 16,
        closed: true
    )

    // Soft outer bloom around the infinity ribbon
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: isTiny ? 10 : 28, color: hexColor(0x38BDF8, alpha: 0.45))
    ctx.addPath(fullInfinityPath)
    ctx.setLineWidth(ribbonWidth)
    ctx.setLineCap(.round)
    ctx.setLineJoin(.round)
    ctx.setStrokeColor(hexColor(0x38BDF8, alpha: 0.25))
    ctx.strokePath()
    ctx.restoreGState()

    // Crisp glass outer border beneath the gradient ribbon (no internal segment artifacts)
    ctx.saveGState()
    ctx.addPath(fullInfinityPath)
    ctx.setLineWidth(ribbonWidth + (isTiny ? 5.0 : 3.5))
    ctx.setLineCap(.round)
    ctx.setLineJoin(.round)
    ctx.setStrokeColor(rgba(255, 255, 255, 0.28))
    ctx.strokePath()
    ctx.restoreGState()

    // Main multi-stop gradient ribbon
    paintRibbonSegment(
        in: ctx,
        path: fullInfinityPath,
        width: ribbonWidth,
        lineCap: .round,
        highlightAlpha: 0.22,
        drawHighlight: !isTiny
    )

    // 3D Over-Under Braid Crossing Detail at (512, 512)
    // Paint subtle undercut shadow onto the under-strand, then paint seamless matching over-strand.
    if !isTiny {
        let underSegment = createSmoothInfinityPath(
            center: center,
            rx: rx,
            ry: ry,
            tStart: (.pi * 1.5) - 0.55,
            tEnd: (.pi * 1.5) + 0.55,
            segments: 4,
            closed: false
        )
        let overSegment = createSmoothInfinityPath(
            center: center,
            rx: rx,
            ry: ry,
            tStart: (.pi * 0.5) - 0.55,
            tEnd: (.pi * 0.5) + 0.55,
            segments: 4,
            closed: false
        )

        // Undercut shadow strictly clipped to the under-strand ribbon
        ctx.saveGState()
        ctx.addPath(underSegment)
        ctx.setLineWidth(ribbonWidth)
        ctx.setLineCap(.butt)
        ctx.setLineJoin(.round)
        ctx.replacePathWithStrokedPath()
        ctx.clip()
        drawRadialGlow(
            in: ctx,
            center: center,
            radius: ribbonWidth * 1.05,
            innerColor: rgba(2, 6, 23, 0.72),
            outerColor: rgba(2, 6, 23, 0.0)
        )
        ctx.restoreGState()

        // Repaint the crossing over-strand with identical gradient & .butt caps so endpoints are invisible
        paintRibbonSegment(
            in: ctx,
            path: overSegment,
            width: ribbonWidth,
            lineCap: .butt,
            highlightAlpha: 0.24,
            drawHighlight: true
        )

        // Subtle edge bevel lines on the crossing over-strand only (clipped cleanly at ends)
        let overBevel = createSmoothInfinityPath(
            center: center,
            rx: rx,
            ry: ry,
            tStart: (.pi * 0.5) - 0.36,
            tEnd: (.pi * 0.5) + 0.36,
            segments: 4,
            closed: false
        )
        ctx.saveGState()
        ctx.addPath(overBevel)
        ctx.setLineWidth(ribbonWidth + 2.0)
        ctx.setLineCap(.butt)
        ctx.setStrokeColor(rgba(255, 255, 255, 0.14))
        ctx.strokePath()
        ctx.restoreGState()

        paintRibbonSegment(
            in: ctx,
            path: overSegment,
            width: ribbonWidth - 1.0,
            lineCap: .butt,
            highlightAlpha: 0.24,
            drawHighlight: true
        )
    }

    // Dual GPU / Tensor Focal Cores inside left & right apertures
    let leftFocus = CGPoint(x: 344, y: 512)
    let rightFocus = CGPoint(x: 680, y: 512)

    drawRadialGlow(
        in: ctx,
        center: leftFocus,
        radius: 68,
        innerColor: hexColor(0x38BDF8, alpha: 0.55),
        outerColor: hexColor(0x38BDF8, alpha: 0.0)
    )
    ctx.setFillColor(hexColor(0xE0F2FE, alpha: 0.92))
    ctx.fillEllipse(in: CGRect(x: leftFocus.x - 14, y: leftFocus.y - 14, width: 28, height: 28))
    ctx.setStrokeColor(hexColor(0x38BDF8, alpha: 0.75))
    ctx.setLineWidth(isTiny ? 5.0 : 3.0)
    ctx.strokeEllipse(in: CGRect(x: leftFocus.x - 28, y: leftFocus.y - 28, width: 56, height: 56))

    drawRadialGlow(
        in: ctx,
        center: rightFocus,
        radius: 68,
        innerColor: hexColor(0xFBBF24, alpha: 0.55),
        outerColor: hexColor(0xFBBF24, alpha: 0.0)
    )
    ctx.setFillColor(hexColor(0xFEF3C7, alpha: 0.94))
    ctx.fillEllipse(in: CGRect(x: rightFocus.x - 14, y: rightFocus.y - 14, width: 28, height: 28))
    ctx.setStrokeColor(hexColor(0xF59E0B, alpha: 0.78))
    ctx.setLineWidth(isTiny ? 5.0 : 3.0)
    ctx.strokeEllipse(in: CGRect(x: rightFocus.x - 28, y: rightFocus.y - 28, width: 56, height: 56))

    // 7. Crisp Inner Frosted Glass Rim around the outer squircle edge
    ctx.saveGState()
    ctx.addPath(squirclePath)
    ctx.setLineWidth(isTiny ? 22.0 : (isSmall ? 14.0 : 8.5))
    ctx.setLineJoin(.round)
    ctx.replacePathWithStrokedPath()
    ctx.clip()
    if let rimGrad = CGGradient(
        colorsSpace: srgb,
        colors: [
            rgba(255, 255, 255, 0.34),
            rgba(186, 230, 253, 0.16),
            rgba(148, 163, 184, 0.07),
            rgba(251, 191, 36, 0.12),
        ] as CFArray,
        locations: [0.0, 0.3, 0.72, 1.0]
    ) {
        ctx.drawLinearGradient(
            rimGrad,
            start: CGPoint(x: 512, y: tileRect.maxY),
            end: CGPoint(x: 512, y: tileRect.minY),
            options: []
        )
    }
    ctx.restoreGState()

    // Outer subtle dark hairline for crisp separation on light & dark wallpapers
    ctx.restoreGState() // end squircle clip
    ctx.saveGState()
    ctx.addPath(squirclePath)
    ctx.setLineWidth(isTiny ? 10.0 : 2.0)
    ctx.setStrokeColor(rgba(2, 6, 23, 0.55))
    ctx.strokePath()
    ctx.restoreGState()

    return ctx.makeImage()
}

private func writePNG(image: CGImage, to url: URL) throws {
    let rep = NSBitmapImageRep(cgImage: image)
    rep.size = NSSize(width: image.width, height: image.height)
    guard let data = rep.representation(using: .png, properties: [:]) else {
        throw NSError(
            domain: "GenerateIcon",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Failed to encode PNG for \(url.lastPathComponent)"]
        )
    }
    try data.write(to: url, options: .atomic)
}

private func main() throws {
    let args = CommandLine.arguments
    if args.contains("--help") || args.contains("-h") {
        print("Usage: swift scripts/macos/generate_icon.swift <output_icns_path>")
        return
    }
    guard args.count >= 2, !args[1].isEmpty else {
        fputs("Error: missing <output_icns_path>.\nUsage: swift scripts/macos/generate_icon.swift <output_icns_path>\n", stderr)
        exit(2)
    }

    let rawOutputPath = args[1]
    var isDir: ObjCBool = false
    let requestedURL = URL(fileURLWithPath: rawOutputPath)
    let outputURL: URL
    if FileManager.default.fileExists(atPath: requestedURL.path, isDirectory: &isDir), isDir.boolValue {
        outputURL = requestedURL.appendingPathComponent("AppIcon.icns")
    } else {
        outputURL = requestedURL
    }

    let tempRoot = FileManager.default.temporaryDirectory
        .appendingPathComponent("colab-icon-\(UUID().uuidString)", isDirectory: true)
    let iconsetDir = tempRoot.appendingPathComponent("AppIcon.iconset", isDirectory: true)
    let tempIcns = tempRoot.appendingPathComponent("AppIcon.icns")

    try FileManager.default.createDirectory(at: iconsetDir, withIntermediateDirectories: true)
    defer {
        try? FileManager.default.removeItem(at: tempRoot)
    }

    var renderedCache: [Int: CGImage] = [:]
    for spec in iconSpecs {
        let image: CGImage
        if let cached = renderedCache[spec.pixelSize] {
            image = cached
        } else {
            guard let generated = drawIcon(pixelSize: spec.pixelSize) else {
                throw NSError(
                    domain: "GenerateIcon",
                    code: 2,
                    userInfo: [NSLocalizedDescriptionKey: "Failed to render icon at \(spec.pixelSize)x\(spec.pixelSize)"]
                )
            }
            renderedCache[spec.pixelSize] = generated
            image = generated
        }
        let pngURL = iconsetDir.appendingPathComponent(spec.filename)
        try writePNG(image: image, to: pngURL)
    }

    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
    process.arguments = ["-c", "icns", iconsetDir.path, "-o", tempIcns.path]

    let stderrPipe = Pipe()
    process.standardError = stderrPipe
    try process.run()
    process.waitUntilExit()

    guard process.terminationStatus == 0 else {
        let errData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
        let errStr = String(data: errData, encoding: .utf8) ?? "unknown error"
        throw NSError(
            domain: "GenerateIcon",
            code: Int(process.terminationStatus),
            userInfo: [NSLocalizedDescriptionKey: "iconutil failed: \(errStr)"]
        )
    }

    try FileManager.default.createDirectory(
        at: outputURL.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    if FileManager.default.fileExists(atPath: outputURL.path) {
        try FileManager.default.removeItem(at: outputURL)
    }
    try FileManager.default.copyItem(at: tempIcns, to: outputURL)
    print("Generated macOS icon: \(outputURL.path)")
}

do {
    try main()
} catch {
    fputs("Error: \(error.localizedDescription)\n", stderr)
    exit(1)
}
