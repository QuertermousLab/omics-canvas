// Connection settings shared by Omics Canvas.app and Genome Canvas.app: the server address and port, and an optional
// SSH tunnel (port forwarding) with an account and password (Keychain) for servers that are not reachable directly.
#import <AppKit/AppKit.h>

NS_ASSUME_NONNULL_BEGIN

@interface OCConnection : NSObject
/** where the gateway is, as seen from this Mac (direct) or from the SSH host (tunnel) */
@property(nonatomic, copy) NSString *host;
@property(nonatomic) NSInteger port;
/** page path on the gateway: "/" (Omics Canvas) or "/genome-canvas/" */
@property(nonatomic, copy) NSString *path;
@property(nonatomic, copy) NSString *scheme;
@property(nonatomic) BOOL useTunnel;
@property(nonatomic, copy) NSString *sshHost;
@property(nonatomic) NSInteger sshPort;
@property(nonatomic, copy) NSString *sshUser;
/** the tunnel's local end, kept across launches so the address (and its cookies) stays the same */
@property(nonatomic) NSInteger localPort;
@property(nonatomic, copy, nullable) void (^onTunnelClosed)(NSString *message);
@property(nonatomic, readonly) BOOL tunnelRunning;

/** Settings saved under `prefix` in the user defaults; a legacy full address (older versions) is read once. */
+ (instancetype)connectionWithDefaultsPrefix:(NSString *)prefix defaultAddress:(NSString *)address legacyAddress:(nullable NSString *)legacy;
- (void)save;
- (void)resetToDefault;
/** scheme://host:port/path as configured (no tunnel) */
- (NSURL *)directURL;
/** "171.65.68.140:8892" or "171.65.68.140:8892 · SSH quanyiz@server" */
- (NSString *)summary;

/** the SSH password in the Keychain ("" when none) */
- (NSString *)password;
- (void)setPassword:(NSString *)password;

/** The page URL to load: the direct address, or — with a tunnel — http://127.0.0.1:<local port>/path once ssh is up. */
- (void)openWithCompletion:(void (^)(NSURL *_Nullable url, NSString *_Nullable error))completion;
- (void)close;

/** The settings sheet (Server, Port, SSH port forwarding, Account, Password); `done` is called with YES after Connect. */
- (void)editInWindow:(NSWindow *)window title:(NSString *)title message:(NSString *)message done:(void (^)(BOOL connect))done;
@end

/** Call first in main(): when ssh runs this app as its SSH_ASKPASS helper it prints the Keychain password and exits. */
void OCRunAskpassIfRequested(void);

NS_ASSUME_NONNULL_END
