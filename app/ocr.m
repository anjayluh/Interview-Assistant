#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <Vision/Vision.h>

int main(void) {
    @autoreleasepool {
        NSData *image = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
        CGImageSourceRef source = CGImageSourceCreateWithData((__bridge CFDataRef)image, NULL);
        if (!source) return 1;
        NSDictionary *properties = CFBridgingRelease(
            CGImageSourceCopyPropertiesAtIndex(source, 0, NULL));
        NSUInteger width = [properties[(__bridge NSString *)kCGImagePropertyPixelWidth] unsignedIntegerValue];
        NSUInteger height = [properties[(__bridge NSString *)kCGImagePropertyPixelHeight] unsignedIntegerValue];
        if (!width || !height || width > 8000 || height > 8000 || width * height > 24000000) {
            CFRelease(source);
            return 1;
        }
        CGImageRef pixels = CGImageSourceCreateImageAtIndex(source, 0, NULL);
        CFRelease(source);
        if (!pixels) return 1;

        VNRecognizeTextRequest *request = [VNRecognizeTextRequest new];
        request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        request.usesLanguageCorrection = NO;
        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:pixels options:@{}];
        CGImageRelease(pixels);
        if (![handler performRequests:@[request] error:NULL]) return 1;
        for (VNRecognizedTextObservation *line in request.results) {
            NSString *text = [line topCandidates:1].firstObject.string;
            if (text) puts(text.UTF8String);
        }
    }
    return 0;
}
