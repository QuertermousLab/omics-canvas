// See OCConnection.h. The SSH tunnel is /usr/bin/ssh -L 127.0.0.1:<local>:<host>:<port>; a password is handed to ssh
// by this app itself (SSH_ASKPASS = the app's executable, which prints the Keychain entry and exits), so it never
// appears on a command line, in the environment or in a file. Without a password ssh uses the user's keys / agent.
#import "OCConnection.h"
#import <Security/Security.h>
#import <objc/runtime.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

static NSString *const OCAskpassServiceEnv = @"OC_ASKPASS_SERVICE";
static NSString *const OCAskpassAccountEnv = @"OC_ASKPASS_ACCOUNT";

static NSString *OCKeychainService(void) {
    return [NSString stringWithFormat:@"%@.ssh", NSBundle.mainBundle.bundleIdentifier ?: @"omics-canvas"];
}

static NSDictionary *OCKeychainQuery(NSString *service, NSString *account) {
    return @{(__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
             (__bridge id)kSecAttrService: service,
             (__bridge id)kSecAttrAccount: account};
}

static NSString *OCKeychainRead(NSString *service, NSString *account) {
    NSMutableDictionary *query = [OCKeychainQuery(service, account) mutableCopy];
    query[(__bridge id)kSecReturnData] = @YES;
    query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef result = NULL;
    if (SecItemCopyMatching((__bridge CFDictionaryRef)query, &result) != errSecSuccess || !result) return @"";
    NSData *data = (__bridge_transfer NSData *)result;
    return [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
}

static void OCKeychainWrite(NSString *service, NSString *account, NSString *password) {
    NSDictionary *query = OCKeychainQuery(service, account);
    SecItemDelete((__bridge CFDictionaryRef)query);
    if (!password.length) return;
    NSMutableDictionary *item = [query mutableCopy];
    item[(__bridge id)kSecValueData] = [password dataUsingEncoding:NSUTF8StringEncoding];
    item[(__bridge id)kSecAttrLabel] = [NSString stringWithFormat:@"%@ SSH port forwarding (%@)", NSBundle.mainBundle.infoDictionary[@"CFBundleName"] ?: @"Omics Canvas", account];
    SecItemAdd((__bridge CFDictionaryRef)item, NULL);
}

void OCRunAskpassIfRequested(void) {
    NSDictionary *env = NSProcessInfo.processInfo.environment;
    NSString *service = env[OCAskpassServiceEnv], *account = env[OCAskpassAccountEnv];
    if (!service.length || !account.length) return;
    @autoreleasepool {
        NSString *password = OCKeychainRead(service, account);
        // ssh asks once (NumberOfPasswordPrompts=1); anything else it might ask (a key passphrase) gets no answer
        if (password.length) {
            NSData *data = [[password stringByAppendingString:@"\n"] dataUsingEncoding:NSUTF8StringEncoding];
            fwrite(data.bytes, 1, data.length, stdout);
        }
    }
    exit(0);
}

static BOOL OCPortFree(NSInteger port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return NO;
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    BOOL free = bind(fd, (struct sockaddr *)&address, sizeof(address)) == 0;
    close(fd);
    return free;
}

static NSInteger OCAnyFreePort(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return 0;
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    socklen_t length = sizeof(address);
    NSInteger port = 0;
    if (bind(fd, (struct sockaddr *)&address, sizeof(address)) == 0 && getsockname(fd, (struct sockaddr *)&address, &length) == 0) port = ntohs(address.sin_port);
    close(fd);
    return port;
}

static BOOL OCPortAnswers(NSInteger port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return NO;
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    BOOL ok = connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0;
    close(fd);
    return ok;
}

/** A control target that runs a block (kept alive as an associated object of its control). */
@interface OCControlBlock : NSObject
@property(nonatomic, copy) void (^block)(void);
@end
@implementation OCControlBlock
- (void)run:(id)sender { if (self.block) self.block(); }
@end

@interface OCConnection ()
@property(nonatomic, copy) NSString *prefix;
@property(nonatomic, copy) NSString *defaultAddress;
@property(nonatomic, strong, nullable) NSTask *task;
@property(nonatomic, strong, nullable) NSPipe *errors;
@property(nonatomic) BOOL closing;
@end

@implementation OCConnection

+ (instancetype)connectionWithDefaultsPrefix:(NSString *)prefix defaultAddress:(NSString *)address legacyAddress:(NSString *)legacy {
    OCConnection *c = [[OCConnection alloc] init];
    c.prefix = prefix;
    c.defaultAddress = address;
    [c resetToDefault];
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    NSString *(^key)(NSString *) = ^NSString *(NSString *name) { return [prefix stringByAppendingString:name]; };
    if ([d objectForKey:key(@"Host")]) {
        c.host = [d stringForKey:key(@"Host")] ?: c.host;
        c.port = [d integerForKey:key(@"Port")] ?: c.port;
        c.scheme = [d stringForKey:key(@"Scheme")] ?: c.scheme;
        c.path = [d stringForKey:key(@"Path")] ?: c.path;
        c.useTunnel = [d boolForKey:key(@"UseTunnel")];
        c.sshHost = [d stringForKey:key(@"SSHHost")] ?: @"";
        c.sshPort = [d integerForKey:key(@"SSHPort")] ?: 22;
        c.sshUser = [d stringForKey:key(@"SSHUser")] ?: @"";
        c.localPort = [d integerForKey:key(@"LocalPort")];
    } else if (legacy.length) {
        [c applyAddress:legacy];  // an address saved by an older version
    }
    return c;
}

- (void)applyAddress:(NSString *)address {
    NSString *candidate = [address stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if ([candidate rangeOfString:@"://"].location == NSNotFound) candidate = [@"http://" stringByAppendingString:candidate];
    NSURLComponents *u = [NSURLComponents componentsWithString:candidate];
    if (!u.host.length) return;
    self.scheme = u.scheme.lowercaseString ?: @"http";
    self.host = u.host;
    self.port = u.port ? u.port.integerValue : ([self.scheme isEqualToString:@"https"] ? 443 : 80);
    NSString *path = u.path.length ? u.path : @"/";
    if (![path hasSuffix:@"/"]) path = [path stringByAppendingString:@"/"];
    if (![path isEqualToString:@"/"] || [self.path isEqualToString:@"/"]) self.path = path;
}

- (void)resetToDefault {
    self.scheme = @"http";
    self.path = @"/";
    [self applyAddress:self.defaultAddress];
    self.useTunnel = NO;
    self.sshHost = @"";
    self.sshPort = 22;
    self.sshUser = @"";
}

- (void)save {
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    NSString *(^key)(NSString *) = ^NSString *(NSString *name) { return [self.prefix stringByAppendingString:name]; };
    [d setObject:self.host forKey:key(@"Host")];
    [d setInteger:self.port forKey:key(@"Port")];
    [d setObject:self.scheme forKey:key(@"Scheme")];
    [d setObject:self.path forKey:key(@"Path")];
    [d setBool:self.useTunnel forKey:key(@"UseTunnel")];
    [d setObject:self.sshHost ?: @"" forKey:key(@"SSHHost")];
    [d setInteger:self.sshPort forKey:key(@"SSHPort")];
    [d setObject:self.sshUser ?: @"" forKey:key(@"SSHUser")];
    [d setInteger:self.localPort forKey:key(@"LocalPort")];
}

- (NSURL *)directURL {
    NSURLComponents *u = [[NSURLComponents alloc] init];
    u.scheme = self.scheme;
    u.host = self.host;
    BOOL standard = ([self.scheme isEqualToString:@"http"] && self.port == 80) || ([self.scheme isEqualToString:@"https"] && self.port == 443);
    if (!standard) u.port = @(self.port);
    u.path = self.path;
    return u.URL;
}

- (NSString *)effectiveSSHHost { return self.sshHost.length ? self.sshHost : self.host; }

- (NSString *)keychainAccount {
    return [NSString stringWithFormat:@"%@@%@:%ld", self.sshUser, [self effectiveSSHHost], (long)self.sshPort];
}

- (NSString *)summary {
    NSString *server = [NSString stringWithFormat:@"%@:%ld", self.host, (long)self.port];
    if (!self.useTunnel) return server;
    NSString *ssh = self.sshUser.length ? [NSString stringWithFormat:@"%@@%@", self.sshUser, [self effectiveSSHHost]] : [self effectiveSSHHost];
    return [NSString stringWithFormat:@"%@ · SSH %@", server, ssh];
}

- (NSString *)password { return self.sshUser.length ? OCKeychainRead(OCKeychainService(), [self keychainAccount]) : @""; }
- (void)setPassword:(NSString *)password { if (self.sshUser.length) OCKeychainWrite(OCKeychainService(), [self keychainAccount], password ?: @""); }

- (BOOL)tunnelRunning { return self.task.running; }

#pragma mark Tunnel

- (void)close {
    self.closing = YES;
    if (self.task.running) [self.task terminate];
    self.task = nil;
    self.closing = NO;
}

- (void)openWithCompletion:(void (^)(NSURL *, NSString *))completion {
    if (!self.useTunnel) { [self close]; completion([self directURL], nil); return; }
    if (self.task.running && self.localPort && OCPortAnswers(self.localPort)) { completion([self tunnelURL], nil); return; }
    [self close];
    if (!self.localPort || !OCPortFree(self.localPort)) self.localPort = OCAnyFreePort();
    if (!self.localPort) { completion(nil, @"No free local port for the SSH tunnel"); return; }
    [self save];

    BOOL password = self.password.length > 0;
    // the gateway as seen from the SSH host: the host itself when the tunnel ends on the server
    NSString *target = [[self effectiveSSHHost] caseInsensitiveCompare:self.host] == NSOrderedSame ? @"127.0.0.1" : self.host;
    NSMutableArray<NSString *> *args = [@[@"-N", @"-T",
        @"-o", @"ExitOnForwardFailure=yes", @"-o", @"ServerAliveInterval=20", @"-o", @"ServerAliveCountMax=3",
        @"-o", @"ConnectTimeout=15", @"-o", @"StrictHostKeyChecking=accept-new", @"-o", @"NumberOfPasswordPrompts=1",
        @"-p", [NSString stringWithFormat:@"%ld", (long)self.sshPort],
        @"-L", [NSString stringWithFormat:@"127.0.0.1:%ld:%@:%ld", (long)self.localPort, target, (long)self.port]] mutableCopy];
    if (!password) [args addObjectsFromArray:@[@"-o", @"BatchMode=yes"]];  // keys / agent only, never a prompt
    [args addObject:self.sshUser.length ? [NSString stringWithFormat:@"%@@%@", self.sshUser, [self effectiveSSHHost]] : [self effectiveSSHHost]];

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/ssh"];
    task.arguments = args;
    NSMutableDictionary *env = [NSProcessInfo.processInfo.environment mutableCopy];
    if (password) {
        env[@"SSH_ASKPASS"] = NSBundle.mainBundle.executablePath;
        env[@"SSH_ASKPASS_REQUIRE"] = @"force";
        env[@"DISPLAY"] = env[@"DISPLAY"] ?: @":0";
        env[OCAskpassServiceEnv] = OCKeychainService();
        env[OCAskpassAccountEnv] = [self keychainAccount];
    }
    task.environment = env;
    task.standardInput = NSFileHandle.fileHandleWithNullDevice;
    task.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    self.errors = [NSPipe pipe];
    task.standardError = self.errors;
    __weak OCConnection *weakSelf = self;
    task.terminationHandler = ^(NSTask *finished) {
        dispatch_async(dispatch_get_main_queue(), ^{
            OCConnection *strongSelf = weakSelf;
            if (!strongSelf || strongSelf.closing || finished != strongSelf.task) return;
            strongSelf.task = nil;
            if (strongSelf.onTunnelClosed) strongSelf.onTunnelClosed([strongSelf lastError] ?: @"The SSH tunnel closed");
        });
    };
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) { completion(nil, [NSString stringWithFormat:@"Could not start ssh: %@", error.localizedDescription]); return; }
    self.task = task;
    [self waitForTunnel:task attempt:0 completion:completion];
}

- (NSURL *)tunnelURL {
    NSURLComponents *u = [[NSURLComponents alloc] init];
    u.scheme = @"http";
    u.host = @"127.0.0.1";
    u.port = @(self.localPort);
    u.path = self.path;
    return u.URL;
}

- (nullable NSString *)lastError {
    NSData *data = [self.errors.fileHandleForReading availableData];
    NSString *text = [[NSString alloc] initWithData:data ?: [NSData data] encoding:NSUTF8StringEncoding];
    NSArray<NSString *> *lines = [[text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] componentsSeparatedByString:@"\n"];
    for (NSString *line in lines.reverseObjectEnumerator) if (line.length && ![line hasPrefix:@"Warning: Permanently added"]) return line;
    return nil;
}

- (void)waitForTunnel:(NSTask *)task attempt:(NSInteger)attempt completion:(void (^)(NSURL *, NSString *))completion {
    if (task != self.task) return;
    if (!task.running) {
        NSString *message = [self lastError] ?: @"ssh stopped";
        self.task = nil;
        completion(nil, [NSString stringWithFormat:@"SSH port forwarding failed: %@", message]);
        return;
    }
    if (OCPortAnswers(self.localPort)) { completion([self tunnelURL], nil); return; }
    if (attempt >= 120) {  // 30 s
        [self close];
        completion(nil, @"SSH port forwarding did not come up within 30 seconds");
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.25 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self waitForTunnel:task attempt:attempt + 1 completion:completion];
    });
}

#pragma mark Settings sheet


static NSTextField *OCLabel(NSString *text) {
    NSTextField *label = [NSTextField labelWithString:text];
    label.alignment = NSTextAlignmentRight;
    return label;
}

static NSTextField *OCField(NSString *value, NSString *placeholder, CGFloat width) {
    NSTextField *field = [NSTextField textFieldWithString:value ?: @""];
    field.placeholderString = placeholder;
    [field.widthAnchor constraintEqualToConstant:width].active = YES;
    return field;
}

- (void)editInWindow:(NSWindow *)window title:(NSString *)title message:(NSString *)message done:(void (^)(BOOL))done {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = title;
    alert.informativeText = message;
    [alert addButtonWithTitle:@"Connect"];
    [alert addButtonWithTitle:@"Cancel"];
    [alert addButtonWithTitle:@"Use Default"];

    NSTextField *host = OCField(self.host, @"171.65.68.140", 230);
    NSTextField *port = OCField([NSString stringWithFormat:@"%ld", (long)self.port], @"8892", 70);
    NSButton *tunnel = [NSButton checkboxWithTitle:@"Connect through SSH port forwarding" target:nil action:nil];
    tunnel.state = self.useTunnel ? NSControlStateValueOn : NSControlStateValueOff;
    NSTextField *sshHost = OCField(self.sshHost, @"same as the server", 230);
    NSTextField *sshPort = OCField([NSString stringWithFormat:@"%ld", (long)self.sshPort], @"22", 70);
    NSTextField *user = OCField(self.sshUser, @"your account on the SSH host", 230);
    NSSecureTextField *password = [[NSSecureTextField alloc] initWithFrame:NSZeroRect];
    password.stringValue = self.password;
    password.placeholderString = @"optional — empty: your SSH key";
    [password.widthAnchor constraintEqualToConstant:230].active = YES;
    NSTextField *note = [NSTextField wrappingLabelWithString:@"The server address and port are those of the gateway as the SSH host sees it (the same machine: its own address works). The password is kept in your macOS Keychain."];
    note.font = [NSFont systemFontOfSize:11];
    note.textColor = NSColor.secondaryLabelColor;
    [note.widthAnchor constraintEqualToConstant:400].active = YES;

    NSStackView *(^row)(NSArray<NSView *> *) = ^NSStackView *(NSArray<NSView *> *views) {
        NSStackView *stack = [NSStackView stackViewWithViews:views];
        stack.orientation = NSUserInterfaceLayoutOrientationHorizontal;
        stack.spacing = 8;
        return stack;
    };
    NSGridView *grid = [NSGridView gridViewWithViews:@[
        @[OCLabel(@"Server"), row(@[host, OCLabel(@"Port"), port])],
        @[[NSGridCell emptyContentView], tunnel],
        @[OCLabel(@"SSH host"), row(@[sshHost, OCLabel(@"Port"), sshPort])],
        @[OCLabel(@"Account"), user],
        @[OCLabel(@"Password"), password],
        @[[NSGridCell emptyContentView], note],
    ]];
    grid.rowSpacing = 8;
    grid.columnSpacing = 8;
    [grid columnAtIndex:0].xPlacement = NSGridCellPlacementTrailing;
    grid.translatesAutoresizingMaskIntoConstraints = NO;
    // the accessory view is exactly as tall as its fields, so no gap opens above the buttons
    NSSize fit = grid.fittingSize;
    NSView *container = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, ceil(MAX(fit.width, 420)), ceil(fit.height))];
    [container addSubview:grid];
    [NSLayoutConstraint activateConstraints:@[
        [grid.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
        [grid.topAnchor constraintEqualToAnchor:container.topAnchor],
    ]];
    NSArray<NSControl *> *sshControls = @[sshHost, sshPort, user, password];
    __weak NSButton *weakTunnel = tunnel;
    void (^sync)(void) = ^{ for (NSControl *c in sshControls) c.enabled = weakTunnel.state == NSControlStateValueOn; };
    sync();
    OCControlBlock *toggle = [[OCControlBlock alloc] init];
    toggle.block = sync;
    objc_setAssociatedObject(tunnel, @selector(run:), toggle, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    tunnel.target = toggle;
    tunnel.action = @selector(run:);
    alert.accessoryView = container;
    [alert layout];
    alert.window.initialFirstResponder = host;

    [alert beginSheetModalForWindow:window completionHandler:^(NSModalResponse response) {
        if (response == NSAlertThirdButtonReturn) {
            [self resetToDefault];
            [self save];
            done(YES);
            return;
        }
        if (response != NSAlertFirstButtonReturn) { done(NO); return; }
        NSString *(^trim)(NSString *) = ^NSString *(NSString *s) { return [s stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet]; };
        // the Server field takes a host, "host:port" or a whole address ("http://host:port/path")
        NSString *typed = trim(host.stringValue);
        NSString *full = [typed rangeOfString:@"://"].location == NSNotFound ? [@"http://" stringByAppendingString:typed] : typed;
        NSURLComponents *parsed = typed.length ? [NSURLComponents componentsWithString:full] : nil;
        if (parsed.host.length) [self applyAddress:typed];
        NSInteger p = trim(port.stringValue).integerValue;
        if (!parsed.port && p > 0 && p < 65536) self.port = p;
        self.useTunnel = tunnel.state == NSControlStateValueOn;
        self.sshHost = trim(sshHost.stringValue);
        NSInteger sp = trim(sshPort.stringValue).integerValue;
        self.sshPort = sp > 0 && sp < 65536 ? sp : 22;
        self.sshUser = trim(user.stringValue);
        if (self.useTunnel) [self setPassword:password.stringValue];
        [self save];
        done(YES);
    }];
}

@end
