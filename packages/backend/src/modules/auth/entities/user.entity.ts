import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
  OneToMany,
} from 'typeorm';
import { RefreshToken } from './refresh-token.entity';

@Entity('users')
export class User {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'varchar', length: 255, unique: true })
  email: string;

  @Column({ type: 'varchar', length: 255, name: 'password_hash' })
  passwordHash: string;

  @Column({ type: 'varchar', length: 50, default: 'trader' })
  role: string;

  /** Disabled users keep their data but cannot log in. */
  @Column({ type: 'boolean', name: 'is_active', default: true })
  isActive: boolean;

  /** Per-user UI theme (colour mode + accent). Null until the user picks one,
   *  in which case the client falls back to its default (dark / indigo). */
  @Column({ type: 'jsonb', nullable: true })
  theme: { mode?: string; accent?: string } | null;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ type: 'timestamptz', name: 'updated_at' })
  updatedAt: Date;

  @OneToMany(() => RefreshToken, (token) => token.user)
  refreshTokens: RefreshToken[];
}
